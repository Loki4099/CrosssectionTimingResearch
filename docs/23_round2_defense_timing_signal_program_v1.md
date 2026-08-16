# 第二轮：防御时点信号筛选与模型比较计划 v1

更新日期：2026-08-16  
计划 ID：`defense_timing_round2_v1`  
状态：**planning / 第一版计划；尚未形成机器预注册，不得据此启动正式实验**

关联材料：[第一轮主网格总结](./22_round1_main_grid_synthesis.md) · [数据契约与 QA](./02_data_contract_and_qa.md) · [实验台账规范](./03_experiment_ledger.md)

## 1. 决策背景

第一轮已经回答了一个重要但有限的问题：波动率信号能够识别一部分需要降低风险的时期，但已测试的连续缩放和严格 Q4 减仓没有同时解决 long-only 的收益与回撤；直接切反转则不适合作为 long-only 防御动作。动态波动率规则相对同平均暴露或同实现波动率的静态控制也普遍落后，说明失败不能只归因于“少持有美股”，动态进出时点本身也没有提供稳定增量。

第二轮因此不再同时搜索“何时防御”和“如何防御”。本计划先把问题缩小为：

> 在周频、下一交易日开盘执行的严格因果口径下，哪些低维市场信号或受限模型能够识别 SPY 相对现金的进攻与防御时点？

只有一个信号流程通过独立的信号能力、经济价值和稳定性门槛后，才进入 `mom_255_0` 横截面动量的迁移实验。防御资产选择、债券久期、复杂仓位曲线、杠杆和反转不在本轮共同搜索。

## 2. 第一轮结论如何约束第二轮

第二轮继承以下结论，而不重新搜索已经失败的九宫格：

1. `mom_255_0` 是后续唯一横截面动量信号；`mom_12_1` 与 `mom_255_21` 不参加第二轮模型选择。
2. direct reversal 从 long-only 防御动作候选中退出；G21–G23 的 long-short 结果保留为 WML 机制证据，但不进入个人多头部署路线。
3. SPY RV21 是波动率家族的基准变量；book RV126 和 book forecast 不再各自形成大网格。它们在第一轮与动作强度混合，不能被宣称为纯粹的窗口优劣检验。
4. “降低 MDD”不等于“识别了防守时点”。任何新信号都必须与同平均风险暴露的静态控制比较。
5. 2018–2026 已被反复观察。第二轮历史结果只能称为开发期或回溯证据；真正确认必须来自冻结后的 paper/live forward，或从未用于研究决策的外部市场/时期。

## 3. 研究问题与假设

### 3.1 核心问题

- **Q1：方向能力。** 信号能否预测下一周 SPY 相对 T-bill 的收益方向与幅度？
- **Q2：尾部能力。** 信号能否识别随后四周从可成交开盘起的最差相对现金路径？
- **Q3：经济价值。** 在相同防御频率和仓位映射下，动态信号能否在成本后优于同平均暴露的静态 SPY/T-bill 组合？
- **Q4：模型增量。** 受限非线性模型或两状态模型能否稳定超过简单规则、Ridge/Logistic 和低自由度 GAM？
- **Q5：迁移能力。** 唯一晋级信号能否在不改变 `mom_255_0` 选股的情况下改善其收益—回撤权衡？

### 3.2 可证伪假设

- **H1 — 信号信息：** 以 T2 为唯一选模目标的外层 walk-forward 概率优于历史基准概率；同一防御分数与下一周超额收益、未来四周最差超额路径的关系方向正确，且不是单一危机贡献的结果。
- **H2 — 择时价值：** 统一 100%/50% SPY 仓位策略在主成本后，相对同平均暴露静态控制的累计主动财富为正，并在至少 60% 的完整外层年度为正。
- **H3 — 简约优先：** 复杂模型只有在外层结果超过最近简单模型至少一个标准误、概率校准不恶化且危机留一结论不翻转时才可晋级。
- **H4 — 动量迁移：** 预先保存的 OOS 防御信号用于 `mom_255_0` 后，相对裸策略与同平均暴露静态控制都产生正的成本后择时增量，同时保留有意义的下跌保护。

R2D 只有在 champion 同时通过 H1、H2 与 lockbox 门时才启动；champion 为复杂模型时还必须通过 H3。任一必要门失败或不确定即记为 `no_transfer`，不得改阈值、启用 runner-up 或增加模型与特征来救场。

## 4. 范围与非目标

### 4.1 本计划包含

- 扩展并冻结 SPY 总回报 OHLC、日频 T-bill/RF 和 XNYS 日历；
- 在数据合格时扩展 PIT S&P 500 成分、价格与成交量，用于市场广度增量检验；
- 单因子与预定义特征块的纯信号诊断；
- Ridge/Logistic、低自由度 GAM、一个受限 Random Forest、受限 XGBoost 和两状态 HMM；
- 周频 SPY/T-bill 的统一机械仓位策略；
- 唯一晋级信号向 `mom_255_0` 的后续迁移。

### 4.2 本计划明确排除

- LSTM、GRU、TCN、Attention、Informer、TFT、PatchTST 或其他深度学习模型；
- 任意特征子集、模型、窗口、阈值和现金比例的笛卡尔积；
- LightGBM、CatBoost 与 XGBoost 的品牌竞赛；
- 多状态 HMM；两状态失败后不增加状态数；
- short、杠杆、期权、反转和多空 WML；
- SHY/IEF/TLT/BIL/SGOV 等防御资产选择与久期择时；
- 宏观数据的大规模特征库或没有 as-of/vintage 的修订数据；
- 根据 2018Q4、COVID 或 2022 的结果事后调整规则。

## 5. 程序结构与命名空间

Round 2 使用独立命名空间，不扩展已经冻结的 G00–G33，也不复用 XS01。

| 阶段 | 稳定 ID | 目标 | 结束条件 |
|---|---|---|---|
| 数据 | `R2A_DATA` | 建立长样本核心线与 PIT 增量线 | 数据版本、QA 与哈希冻结 |
| 信号 | `R2B_SIGNAL_DIAGNOSTICS` | 单因子、目标与状态诊断 | 特征/目标有效且候选方向冻结 |
| 模型 | `R2C_SPY_TBILL` | 统一 SPY/T-bill 策略与受限模型比较 | 选出至多一个 champion，或全部拒绝 |
| 迁移 | `R2D_MOM255_TRANSFER` | 将 immutable OOS 信号接到 `mom_255_0` | 完成迁移判定，不再回改 R2C |

机器预注册应在本计划冻结后另建：

```text
config/experiments/round2/
experiments/round2_groups.csv
experiments/round2_registry.csv
docs/20_experiments/R2B_signal_diagnostics/design.md
docs/20_experiments/R2C_spy_tbill_timing/design.md
docs/20_experiments/R2D_mom255_transfer/design.md
```

现有 `config/experiments/program.toml` 和 G 组 catalog 不得修改；多条已完成 pipeline 对其 SHA 有冻结门禁。

## 6. R2A：数据扩展与冻结

### 6.1 长样本核心线 `L`

目标范围为 SPY 可交易历史起点 1993-01-29 至数据冻结日；实际起点必须由供应商原始记录与 XNYS 日历核验，不能在 SPY 成立前拼接 SPX 或合成开盘价。

最少字段：

- SPY raw 与 total-return-adjusted open/high/low/close；
- raw volume、拆股、分红、调整因子及调整算法版本；
- 日频 T-bill/RF 的简单收益、对数收益、研究结果可用时点和供应商 snapshot；RF 用于标签、现金 NAV 与事后评价，不默认是同日实时可用特征；
- XNYS 权威会话日历；
- provider、snapshot、下载时间、请求参数、原始记录哈希和 `available_at`。

若 VIX 能取得完整且时点明确的 1993+ 收盘数据，则进入一个预定义的 implied-risk 特征块；否则该块在运行前取消并登记 `invalid_data/not_available`，不能使用短期填补。

R2C 的 SPY 信号、target、执行估值和 NAV 统一使用 total-return-adjusted open/close；raw OHLCV 只用于源 QA、可交易性与成交量特征。普通分红和拆股已经进入 TR 序列，不再向现金或股数 ledger 单独过账，避免双计。

### 6.2 PIT 增量线 `S`

PIT 成分、价格广度和成交量广度形成独立的数据线。目标是尽可能扩展至 1993，但不为了追求起点而接受现代成分回填、幸存者偏差或不明成交量口径。

必须保存：

- 永久证券标识、ticker 历史和 `[effective_from, effective_to)` 成分区间；
- raw 与总回报价格、成交量、分红、拆股、退市和终止事件；
- 每日 PIT 成分数、有效价格数、有效成交量数、排除原因与覆盖率；
- 成交量的供应商、单位、拆股调整政策和跨供应商一致性结果。

当前免费 v3 的个股成交量尚未统一 Yahoo/Tiingo 的拆股调整口径，且基准表没有 SPY volume。因此成交量族在发布 `volume-qualified` 新数据版本前保持 `blocked`。价格广度可先独立验收。

### 6.3 两条数据线的比较纪律

- `L` 线负责长样本核心结论；
- `S` 线只检验广度/成交量是否在共同短区间提供增量；
- 比较 `core` 与 `core+PIT` 时，信号评分只使用完全相同的有效周并重新拟合，但经济路径必须保留同一个连续日历、共同起止开/收盘和全部中间收益，绝不能把两个有效周直接拼接；
- 某个 scheduled PIT week 不合格时，PIT treatment 与 paired core 都不生成该周新分数、不执行 overlay 再平衡并 carry-forward 上次目标；裸策略原有计划调仓仍照常发生。该周不进入两侧模型训练、Brier/IC 或信号分位评分，但日度 NAV、现金收益和后续成本连续保留；
- 共同评价起点本身必须是两侧均有效的 scheduled signal；在第一条有效预测前不得用隐含满仓、零分或事后值填充；
- 共同区间内无效 scheduled weeks 超过 2%，或连续超过 4 周，整个 `PIT01` bundle 记为 `invalid_data`；不得通过缩短空档或另选起点救场；
- 不得把 `S` 线缺失年份填零、填均值或用当前成分代理；
- 若 `S` 线不合格，只取消广度/成交量分支，不阻断 `L` 线。

### 6.4 数据硬门禁

核心线必须满足：

1. `(asset_id, session_date)` 唯一；OHLC 有限、为正且满足价格不变量；
2. 所有 signal close、execution open、T1 endpoints、T3 中间 session close、T3 终点 open 及相应 RF sessions 的覆盖率为 100%；
3. 缺失 RF 不得静默置零或前填；
4. 调整收益、拆股与分红关系通过独立复算；
5. 与当前 v3 重叠区间逐日 reconciliation；差异必须有 snapshot/provider 解释；
6. 两次独立构建产生相同表哈希；
7. 改变未来数据不得改变过去的特征或日历。

T3 整条路径任一点缺失都使该标签无效，不得插值。

PIT 分支使用联合有效掩码：F4 为 `PIT member & valid required price`；F5 为 `PIT member & valid raw/TR price & positive finite volume & no masked-tradability row`。每个进入模型的信号日覆盖率必须不低于 98%、全期中位数必须不低于 99%；不足周预先标记 invalid/missing，并按 6.3 的连续日历/carry-forward 规则同步处理 paired core。成交量单位、拆股或 provider 门禁失败则整个 F5 blocked。任何源、清洗、字段或调整算法变化都必须产生新 `dataset_version`，不得原地扩写 v3。

### 6.5 分阶段授权，避免数据与预注册循环依赖

本 planning 文件只冻结研究架构，不授权下载付费数据。后续按两次门执行：

1. 先单独冻结 `R2A_DATA/design.md`，写明供应商、许可、原始字段、调整算法、QA、可接受缺口和哈希规则；随后只授权数据获取与 QA，不得计算 target、模型分数或策略结果；
2. R2A 数据集冻结后，才由其实际会话生成唯一 decision calendar、feature-complete 起点、outer/inner folds 与绝对 lockbox 日期；
3. 再冻结 R2B/R2C 的 machine design、全部 candidate arm/selector ID、条件数据分支与判定门，之后才授权信号和模型运行；
4. R2D 只能在 R2C 唯一 champion 通过后，凭其不可变哈希启动，且必须在查看任何动量迁移结果前冻结 R2D machine design。

## 7. 周频决策与执行协议

令：

- `s_t` 为该周最后一个 XNYS session 收盘后的信号时点；
- `e_t` 为 `s_t` 后的下一 XNYS session 开盘；
- `e_{t+1}` 与 `e_{t+4}` 分别为下一周和第四周计划执行开盘。

节假日周自然出现周四信号或周二执行，不硬编码 Friday/Monday。

共同规则：

1. 所有特征只使用 `s_t` 收盘时已知的数据；
2. 目标仓位在 `e_t` 开盘执行；`s_t close → e_t open` 属于旧仓位，不计入新信号标签；
3. 每周都恢复目标权重，即使防御状态未改变；
4. 旧仓位先按执行开盘估值，再计算成交额和成本；
5. 开盘后的现金获得当日 RF，之后按同一日频 RF 路径复利；
6. SPY 权重始终在 `[0,1]`，禁止 short 和 leverage；
7. development outer 预测先按日期唯一拼接为一条连续路径，只在其首个 OOS 执行开盘从全现金启动一次；年度边界携带上一交易日 wealth 与持仓，不清仓、不重置 NAV；
8. mechanical lockbox 是单独证据段，只在锁箱首个执行开盘从全现金启动一次，所有静态控制采用相同起点；另可报告从 development 携仓穿越锁箱的连续诊断，但不作为锁箱主判定；
9. 开盘前先得到 `pretrade_NAV` 与漂移后风险资产权重，再计算 `turnover = sum_risky_assets(abs(w_target - w_pre))`、`cost = pretrade_NAV * cost_rate * turnover`；现金不进入 L1，也不重复收费；
10. 主交易成本为每美元 SPY 实际买卖 10bps；0/5/20bps 只作压力情景；现金腿不收费。

若以后改成可交易债券 ETF，必须另立“如何防御”实验并对两条 ETF 腿分别计价差和成本。

## 8. 三个正式目标

所有 target 均为模型无关的毛市场结果；交易成本只在策略层应用。

### T1：下一周连续超额收益

```text
fwd_excess_logret_1w
  = log(SPY_TR_open[e_(t+1)] / SPY_TR_open[e_t])
    - sum(log(1 + rf_d), d in [e_t, e_(t+1)))
```

这是连续收益幅度诊断 target，不参与模型、超参数或阈值选择。它严格从可成交开盘开始，不包含信号收盘到执行开盘的 gap。

### T2：下一周现金胜出分类

```text
cash_wins_1w = 1[fwd_excess_logret_1w < 0]
```

等于零归 risk-on。T2 是 T1 的分类视图，不构成第二份独立经济证据；Brier、log loss 与校准指标用于评价概率质量。

### T3：未来四周最差相对现金路径

从 `e_t` 开盘开始，对起点 0、未来每个 session 收盘以及 `e_{t+4}` 开盘计算 SPY 相对现金的累计对数收益，取最小值。中间收盘和终点开盘分别冻结为：

```text
X_close(d)
  = log(SPY_TR_close[d] / SPY_TR_open[e_t])
    - sum(log(1 + rf_j), j in [e_t, d])

X_open(e_(t+4))
  = log(SPY_TR_open[e_(t+4)] / SPY_TR_open[e_t])
    - sum(log(1 + rf_j), j in [e_t, e_(t+4)))

fwd_worst_excess_4w = min(0, all X_close, X_open)
```

该值不大于零；零表示没有跌破起点相对现金财富，越负表示初始时点后的下行路径越危险。它是 maximum adverse excursion，不是区间内任意峰到谷 MDD；传统 forward MDD 仅作诊断。

`target_end_timestamp` 是上述经济区间终点；`target_available_at` 则是所有相关价格、RF 和调整记录真实可读时间的最大值。T1/T2 只有在 `e_{t+1}` 开盘且所需记录可读后成熟，T3 只有在 `e_{t+4}` 开盘且整条路径记录可读后成熟。训练时必须满足 `target_available_at <= signal_timestamp`；末尾 1/4 周分别标记 censored，不填补。

未来实现波动率、方向 × 路径效率、传统 forward MDD 和 13/26 周结果只作固定诊断，不增加正式 target，也不参与冠军选择。

## 9. 低维特征目录

所有滚动量在 `s_t` 收盘计算。当前值可以包含当日收盘；用于标准化、分位阈值或模型拟合的历史分布只允许使用此前已成熟训练数据。特征统一保存原始值、方向、lookback、`available_at` 和变换版本。

### F1：价格趋势与路径

- `spy_total_return_21d`；
- `spy_total_return_126d`；
- `sma50_over_sma200_minus_1`；
- `drawdown_from_252d_high`。

### F2：实现风险与分布形状

- `log_spy_rv126`；
- `log_rv21_over_rv126`；
- `downside_variance_share_63d`；
- `return_skew_63d`；
- `return_excess_kurtosis_126d`。

`spy_rv21` 继续作为第一轮锚点和单因子 sentinel，但不与 `log_spy_rv126`、`log_rv21_over_rv126` 同时放入多变量 core，避免冗余地重构同一个波动率水平。

### F3：隐含风险，可选数据块

- `variance_risk_gap = (vix_close / 100)^2 - spy_rv21^2`；若供应商已存小数，则 schema 先恢复并记录统一单位约定。

VIX、RV21 和二者差不同时进入多变量模型；F3 只保留上述一个方差差变量。

### F4：PIT 价格广度

- `advance_member_share`；
- `member_share_above_sma200`。

### F5：PIT 方向性成交量

- 每只证券自身严格滞后标准化后的 `signed_dollar_volume_breadth`；
- `down_volume_stress_share`。

成交量先在证券自身历史内标准化、截尾并处理拆股/终止事件，再按当日 PIT 成分聚合。禁止直接求原始股数、原始成交额总和，也禁止在没有 PIT shares outstanding 时声称构造了 turnover。

不搜索任意特征子集。单因子全部报告；模型只使用两个预定义输入版本：

- `core = F1 + F2 + 合格的 F3`；
- `core_plus_pit = core + F4 + 合格的 F5`。

`core_plus_pit` 只在共同短区间与重新拟合的 `core` 配对比较。

## 10. R2B：信号本身的能力

本阶段不先看最高 CAGR。T2 是唯一选模目标；T1 负责收益幅度与方向诊断，T3 负责四周左尾诊断，二者不能在 T2 失败后单独把模型晋级。

每个候选必须同时保存：

- `raw_defense_score`：预先定向为越高越应防御，供 IC、分位与 q75 仓位映射使用；
- `p_cash_wins`：只允许用 inner-OOF sigmoid/Platt 映射 `sigmoid(a + b * z)` 得到，供 Brier 与 log loss 使用。

其中 `z` 是 raw margin；HMM 使用 one-step filtered risk-state probability 的 logit。校准斜率必须 `b > 0`，不得借校准翻转经济方向；校准折只有一类、映射不收敛或 `b <= 0` 时，该候选记为 invalid。禁止 isotonic、校准方法竞赛和训练内校准。

校准采用唯一的 prequential cross-fit：inner validation blocks 按时间排序，第一块只产生 OOF raw score 作为 calibration seed；在第 `j>=2` 块预测前，只用更早 inner blocks 的 OOF raw score/T2 拟合 Platt，再对第 j 块计 Brier。arm 选择只使用这些未参与自身 calibrator 拟合的第 2 块以后分数。arm 选定后，outer-year 最终 calibrator 才可用该 arm 全部已完成 inner-OOF raw score/T2 拟合；模型随后在完整 outer-train 重估并预测该年。不得用同一 OOF 标签既拟合 calibrator 又评价该条校准预测。

### 10.1 方向与概率

- Spearman time-series IC：防御分数对 T1，期望为负；
- Pearson IC 作为辅助；
- T2 的 ROC-AUC、PR-AUC、Brier skill、log loss；
- 概率校准截距、斜率和可靠性图；截距/斜率由 `T2 ~ logit(clip(p_cash_wins, 1e-6, 1-1e-6))` 的 OOS logistic calibration 得到，ECE 固定使用 10 个等频 OOS probability bins；
- 分数五分位内 T1 均值与现金胜出率的单调性。

### 10.2 尾部与路径

- 防御分数对 T3 的 Spearman IC，期望为负；
- T3 最差十分位的 precision、recall、PR-AUC 与误报率；q10 只由对应 outer-train 已成熟 T3 估计并全年冻结，不使用全样本阈值；
- 五分位 T3、未来实现波动率和传统 forward MDD；
- 2018Q4、COVID 下跌/反弹、2022 及数据扩展后预登记危机的首次预警、持续期、漏报和反复切换。

### 10.3 统计纪律

- 这是单一市场的 time-series IC，不称为横截面 IC；
- 重叠四周目标不得使用 IID t-test；
- 主推断使用 13 周 moving-block bootstrap，4/26 周为敏感性；
- 多因子 IC 使用预登记的 FDR/stepdown 校正；
- 阴影、分位带和危机图是诊断，不冒充独立样本置信区间。

## 11. 统一经济代理：SPY/T-bill

所有候选使用相同仓位政策，避免把信号和动作强度混在一起：

```text
if defense_score < expanding_train_q75:
    target_spy_weight = 1.00
else:
    target_spy_weight = 0.50
```

`expanding_train_q75` 只由 outer-train 内最终选中 arm 的全部 inner/prequential OOS `raw_defense_score` 计算，不能混合家族内其他 arms 的尺度，也不能使用模型对自身训练样本的 fitted score；单因子使用严格历史 raw factor value。阈值在映射到该年度首个执行开盘的信号收盘前冻结，全年不变；不针对模型优化防御比例或现金权重。连续分数、概率和 HMM 状态概率均使用同一上四分位告警预算。有效 unique score 不足，或任一完整外层年度产生 0%/100% 告警时，候选记为 invalid，不通过临时切换 `>`/`>=` 救场。该 100%/50% 映射只用于筛选“何时防御”，不代表最终最优动作。

每条动态策略必须完整 replay 两个静态控制：

1. **同平均实际暴露控制：** 使用动态路径在相应证据段内实际持有 session 加权平均暴露；
2. **同实现超额波动控制：** 在 `[0,1]` 内解一个固定 SPY 权重，使其成本前超额波动与动态策略相同；无解则记为 invalid diagnostic，禁止用杠杆补解。

development 与 lockbox 分别估计各自的 ex-post 控制权重，不得把锁箱平均暴露用于 development。两条控制都使用相同开盘、日历、RF、每周再平衡和成本引擎。控制权重属于事后诊断，不伪装为实时可交易参数。主经济量为：

```text
timing_value_t = dynamic_wealth_t / matched_static_wealth_t - 1
```

不得用净值直接相减，也不得只按原始 CAGR 给高平均 beta 的候选加分。

## 12. R2C：模型阶梯与预算

### 12.1 为什么模型按阶梯开放

模型不是同时参赛。先完成 4 个 sentinel、Ridge Logistic selector 和 Logistic GAM selector；只有其中至少一条 outer candidate process 在不含 lockbox 的 development 路径上同时满足 14.1 的全部信号质量门与 14.2 的全部经济价值门，才开放 RF、XGBoost 与 HMM。否则 6 个复杂 trial arms 统一登记 `not_opened_by_preregistered_gate` 并停止 R2C。模型与特征组不得形成全组合搜索。

### 12.2 长样本：17 个注册 trial arms，形成至多 9 条 outer candidate processes

| 数量 | 候选 | T2 主赛道定位 |
|---:|---|---|
| 4 | 预登记单因子 sentinel | RV21、SMA50/200 gap、252 日回撤、21 日 SPY 总回报趋势 |
| 4 | Ridge Logistic | 四个冻结正则强度 |
| 3 | 低自由度 Logistic GAM | 三个冻结平滑惩罚 |
| 1 | 受限 Random Forest | T2 分类的 bagging 敏感性 |
| 4 | 受限 XGBoost | T2 分类，深度 × 树数的冻结小网格 |
| 1 | 两状态 Gaussian HMM | 检验潜在状态持续性 |

表中的 17 是预登记的 T2 candidate-config/trial arm 数量，不是 17 条可在 outer 层自由竞赛的 pipeline。Ridge、GAM 与 XGBoost 的固定 arms 只允许在各自家族的 inner walk-forward 中选择，因而 outer 层至多形成 9 条 candidate processes：4 条 sentinel、1 条 Ridge selector、1 条 GAM selector、1 条 RF、1 条 XGBoost selector 和 1 条 HMM。selector 本身不是新增参数试验；arm ID、selector ID、每个 outer 年选中的 arm 及所有失败/未开放状态都进入 ledger。

T1 与 T3 不单独拟合模型。所有 T1/T3 诊断直接使用对应 T2 outer process 的同一条 OOS `raw_defense_score`；它们只能否决 T2 候选，不能产生 target-specific winner。长样本 development 先产生一个 provisional core winner；仅当它是 Ridge、GAM、RF 或 XGBoost 时，PIT 增量阶段才开放一个额外研究单元 `PIT01 paired bundle`。该 bundle 把 provisional winner 的冻结 family selector 与同一组已登记 arms 原样应用于 `core_plus_pit`，并在完全相同 folds、校准和短区间上重估 `common_period_core` paired control；两侧 selector 可按同一冻结算法选出不同 arm，但不得添加新超参数。paired control 不计作候选，也无权成为 champion；若 provisional winner 是 sentinel 或 HMM，则 `PIT01` 记为 `not_applicable`。合格的 PIT treatment 必须在 lockbox 解封前完成配对 development 比较，之后才能在 provisional core winner 与 PIT treatment 之间冻结最终唯一 candidate。因此研究预算上限是 17 个 long-core arms + 1 个条件 paired bundle，而非任意模型 × 特征搜索。

### 12.3 冻结模型规格

**Ridge Logistic**

- 所有连续变量仅在训练窗标准化；
- 正则强度只允许机器设计冻结的四个对数间隔值并由 inner walk-forward 选择；
- T2 概率校准只能使用 inner OOS 预测，不能用训练内拟合值。

**Logistic GAM**

- additive only，不允许任意交互；
- 每个连续特征采用低自由度平滑，单特征有效自由度上限在机器设计中冻结；
- 平滑惩罚只有三个冻结档位；
- 经济方向明确的变量可在运行前设置单调约束，不能看结果后添加。

**Random Forest**

- 仅一个配置：`n_estimators=1000`、`max_depth=3`、`min_samples_leaf=0.10`、`max_features=sqrt`、bootstrap、固定 seed；
- OOB 只作调试，不参与时序模型选择；
- 不用 class weight 改写目标概率。

**XGBoost**

- `objective=binary:logistic`、`eval_metric=logloss`、`reg_lambda=1`；
- `grow_policy=depthwise`；
- `max_depth in {1, 2}`；
- `n_estimators in {25, 75}`；
- `learning_rate=0.05`；
- `subsample=1`、`colsample_bytree=1`；
- 确定性 exact tree method；
- 每个训练折设置 `min_child_weight = 0.025 * n_train`；在无样本权重的 logistic objective 下，单样本 Hessian 不超过 0.25，因此这是“叶节点至少约 10% 样本”的可执行下界；产物还必须审计每个实际叶节点样本数不低于 `ceil(0.10 * n_train)`，否则该 arm invalid；
- 不使用 DART、Optuna、Bayesian search 或运行后追加参数。

LightGBM 不作为第二个 boosting 假设。其大样本训练效率在本任务中不构成独立研究问题；若以后需要工程替换，只能用已冻结函数容量作一次复现，不参加选冠军。

**两状态 HMM**

- 仅两个状态、对角协方差；
- 当周可观测收益固定为 `hmm_spy_logret_1w(t) = log(SPY_TR_close[s_t] / SPY_TR_close[s_(t-1)])`，其 `available_at = s_t close`；不假设事后 Ken French 同日 RF 已实时可得，也不得误用 `e_t` 之后的 T1；
- 观测固定为上述当周 SPY 总回报、`log_spy_rv126` 和 `log_rv21_over_rv126` 的训练内标准化值；
- 参数只用当年 outer-train 拟合。在 `s_t` 纳入当周观测得到 filtered `pi_t` 后，以 one-step `pi_t @ A` 中预定风险状态的概率作为当周 `raw_defense_score`；测试年度只前向携带 filter state，禁止用后续周回算或使用 full-sample smoothed state；
- 每次年度重估后，必须以新参数的 fitted start distribution 从 outer-train 第一条观测重新 forward-filter 完整 outer-train，使用其末端 posterior 进入首个测试周；禁止把旧参数下的 `pi_t` 跨年带入新参数，也禁止任意改用 stationary probability 重启；
- 风险状态固定映射为训练期 `log_spy_rv126` 条件均值较高的状态；完全相等时按状态索引较大者作为 tie-break，不得用全样本或 T1 命名状态；
- 机器配置固定 10 个确定性 restart、`max_iter=500`、`tol=1e-6` 与标准化尺度上的对角协方差 floor `1e-6`；restart 只按 train log-likelihood 选择，同分取 seed 较小者，不构成额外 arm；
- 任一状态训练期 posterior occupancy 低于 10%、任一转移行的 off-diagonal probability 不在 `[1e-4, 1-1e-4]`、未收敛或出现非有限值时，该 HMM arm invalid；路线结束且不增加状态数。

### 12.4 深度学习解锁条件

本计划不因把 SPY 扩展到 1993 就开放深度学习。滚动窗口数量不等于独立市场状态数量；单一市场每周仍只有一个标签。

只有满足以下条件后，才可另立计划评估一个小型 GRU 或 TCN：

1. 数据变为数十个全球市场/期货的共享面板，或数百只证券的独立资产级 target；
2. 任务确实需要从原始长序列或高维非结构化输入学习表示；
3. 简单模型已在多个 walk-forward 时期稳定达到瓶颈；
4. 冻结的跨市场、跨时期测试能验证成本后增量；
5. 结果对 seed、危机留一和模型小改动稳定。

不同时搜索 LSTM、GRU、Attention、Informer、TFT 和 PatchTST。

## 13. Nested expanding walk-forward

### 13.1 外层

- 核心线从 1993 开始，但 520 周从最终合格 core 的 `first_feature_complete_signal` 起计；`first_outer_signal` 是最大特征预热、520 个 feature-complete 且 label-mature 周和共同 4 周成熟边界之后的下一完整执行年度首笔，准确日期只由冻结日历生成，不预先硬写 2003/2004；
- 外层按 `execution_session.year` 归属完整日历年并 expanding；Y 年 pipeline、变换、校准、阈值和超参数必须在“映射到 Y 年首个 execution open 的 signal close”之前冻结，即使该 signal 位于 Y-1 年末；
- 2026 若只有半年，只单列，不与完整年度通过数混算；
- 所有外层 OOS 周预测拼接为一条唯一 prequential 路径，不平均年度 Sharpe。

PIT 线的晋级资格在全局 `lockbox_start_signal` 前一次性判定：届时必须已经拥有至少 520 个可靠且 label-mature 的训练周，并产生至少 3 个完整的 pre-lockbox development outer-OOS 年。任一条件不满足时，`PIT01` 只能标记 `exploratory/no_champion_eligibility`；不能因为在 lockbox 内或全样本末尾后来凑够 520 周而获得晋级资格。当前 2013+ 免费 v3 单独不满足该资格。

锁箱起点定义为“使其后剩余周信号数不少于 208 的最晚一个完整执行年度首笔信号”，而不是机械切取最后恰好 208 行，避免把同一年拆成选模期与锁箱期。数据冻结时把 `lockbox_start_signal/execution` 与 `lockbox_end_signal/execution` 写成绝对日期；后续数据刷新不得滚动锁箱。所有 family 只用 pre-lockbox development OOS 比较；随后冻结唯一 candidate process、family selector、校准、阈值算法与判定规则，锁箱只运行和揭示该 candidate 一次。若它是 Ridge/GAM/XGBoost selector，锁箱各年度仍按冻结 inner 算法在原登记 arms 中重选并重估，而不是永久沿用 development 最后一年的具体 arm；不得添加 arm 或改变 selector。锁箱通过后它才称为 champion；失败不得启用 runner-up。由于 2018–2026 已被第一轮观察，该锁箱只能约束算法选择，不能声称是纯净外部确认。

### 13.2 内层

- 至少 5 年初始训练；
- 52 周连续验证块；
- 若可用则固定使用最近 5 个 52 周 expanding validation blocks；不足 5 个但至少 3 个时使用全部可用块，少于 3 个时该 candidate process invalid；
- 模型、特征版本、正则、概率校准和阈值均属于 inner pipeline；
- family selector 只能在本节登记的固定 arms 内选择。以各 arm 第 2 块以后的 prequential-calibrated 周度 Brier loss 为唯一 inner 损失；对 `loss_arm - loss_best` 使用固定 13 周 moving-block bootstrap 估计标准误，均值差不超过 1 个标准误者进入 one-SE set；
- one-SE set 内按固定容量顺序选最小者：Ridge 取更大正则，GAM 取更强平滑，XGBoost 为 `(depth1,25) < (depth1,75) < (depth2,25) < (depth2,75)`；完全相同则取稳定 arm ID 字典序最小者，不生成新 arm。

### 13.3 Purge 与标签成熟

- 若首个 validation/test 信号索引为 `v`，每条训练记录先必须满足 `target_available_at <= s_v`；
- 为统一三个目标，边界统一排除 `v` 前 5 个 scheduled signals（4 周 T3 maturity + 1 周 embargo），因此最后允许训练的信号索引为 `v-6`；validation/test 从 `v` 正常开始；
- inner、outer 与 lockbox 使用同一规则，并把实际 signal/date 边界写入 fold manifest；
- 标准化、缺失处理、GAM基函数、校准和特征选择只能在训练窗拟合；
- 若训练折 T2 只有一类，该折记为 invalid，不强行训练；
- 禁止随机 K-fold 和把相邻重叠窗口当独立样本。

## 14. 评分、晋级与停止规则

### 14.1 信号质量门

候选至少满足：

- 每个 outer 年的 class-prior 固定为该年首笔映射信号前、outer-train 内全部已成熟 T2 的均值，全年不更新；aggregate OOS Brier skill 相对这些逐年冻结 base probabilities 拼接后的损失必须为正，这是唯一正式选模损失；
- 防御分数对 T1 与 T3 的 IC 方向正确；
- development OOS 中 defense-score Q5 相对 Q1 必须同时满足：现金胜出率更高、平均 T1 更低、平均 T3 更低；
- OOS 概率校准斜率必须大于 0；截距、可靠性图与 ECE 完整报告，但不允许凭主观判断翻转分数；
- development 整体告警率必须位于 `[5%, 50%]`，且每个完整外层年度均不得为 0% 或 100%。

T1 与 T2 属于同一 target family，不按两份独立证据计票。

### 14.2 Pre-lockbox development 经济价值门

- 10bps 主成本后，相对同平均暴露静态控制的终点 timing value 为正；
- 完整外层年度中至少 60% 为正；
- 相对同实现波动静态控制的终点 timing value 也不得为负；
- 年度贡献固定为动态相对同平均暴露控制的年度对数财富增量；必须存在正贡献年度，且 `max(positive annual contribution) / sum(positive annual contributions) <= 0.50`；
- 数据冻结后、任何 target/model 结果解封前，机器设计必须锁定 dot-com、GFC、COVID 与 2022 等可覆盖危机的准确日期；逐一剔除每个窗口后，aggregate timing value 仍须为正；
- leave-one-crisis-out 只从已经冻结的 OOS loss/return 序列中移除对应日期并重算评价，不重训模型、不重选 arm、不重估阈值；
- 13 周 moving-block bootstrap 区间与多重检验结果完整报告。

### 14.3 复杂模型增量门

- RF/XGBoost/HMM 与 14.4 先确定的 pre-lockbox simple benchmark 只在完全相同 development OOS 周上比较。主差值为逐周 `Brier_loss_simple - Brier_loss_complex`，标准误唯一使用固定 13 周 moving-block bootstrap；复杂模型的平均改善必须至少为该差值的 1 个标准误；
- 复杂度顺序固定为 `sentinel < Ridge < GAM < HMM < RF < XGBoost`；一个标准误以内永远选择顺序更靠前者；
- 复杂模型的 OOS 校准斜率必须大于 0，并相对 simple benchmark 同时满足 `abs(intercept_complex) <= abs(intercept_simple)`、`abs(slope_complex - 1) <= abs(slope_simple - 1)`、`ECE_complex <= ECE_simple`；相对两个静态控制的成本后 timing value 也均不得低于 simple benchmark；
- 每次按 14.2 的固定窗口删除 OOS 评价贡献、但不重训后，`mean(Brier_loss_simple - Brier_loss_complex)` 都必须大于 0；
- inner 只负责家族内 arm 选择；锁箱、inner 或主观图形不得替代上述唯一的 development paired-SE 口径。

`PIT01` 使用同一 paired-SE 规则，只能在共同 pre-lockbox development 周上与 `common_period_core` 比较；它自身必须通过 14.1 与本节的 development-only 14.2，且平均 Brier 改善至少为 1 个固定 13 周 block-bootstrap 标准误，才可替换 provisional core winner。它不在此时读取 lockbox；PIT 线样本较短也不能以“更多特征”为由降低门槛。

RF 与 XGBoost 均未超过简单模型时，树模型路线结束；不再尝试 LightGBM、CatBoost 或更大参数网格。HMM 不稳定时，状态模型路线结束。

### 14.4 确定性的 pre-lockbox 选择算法

1. 所有 long-core outer processes 先裁到完全相同的 feature-complete development OOS 周；14.1/14.2 是否决门，不参与胜者加权打分；
2. 在 4 条 sentinel、Ridge selector、GAM selector 中，仅保留同时通过 14.1 与 14.2 者。以校准后 T2 平均 Brier loss 最低者为 `best_simple`；对每个合格 process 的 `loss_i - loss_best` 用固定 13 周 moving-block bootstrap 估计 paired SE，均值差不超过 1 SE 者进入 one-SE set。先按 `sentinel < Ridge < GAM` 取最低复杂度层，再在该层取平均 Brier 最低者；仅当平均 loss 完全相等时取稳定 process ID 字典序最小者，得到唯一 simple benchmark；
3. 若 simple benchmark 不存在，复杂阶段不开放，R2C 记为 `no_candidate`。若存在，按 14.3 开放复杂 processes。最终候选池只保留：同时通过 14.1/14.2 的 simple processes，以及同时通过 14.1/14.2/14.3 的复杂 processes；若池为空则 `no_candidate`。对该池使用相同 Brier/paired-SE one-SE 规则，先按 `sentinel < Ridge < GAM < HMM < RF < XGBoost` 取最低复杂度层，再取该层平均 Brier 最低者，完全同 loss 才按稳定 ID，得到唯一 provisional core winner；
4. 若 `PIT01` 合格，严格按 14.3 的共同周 paired test 决定它是否替换 provisional core winner；没有达到增量门时保留 core winner；
5. T1、T3、CAGR、Sharpe、MDD、timing value 和图形只作硬门或报告，永远不用于在两个已过门候选之间排序。任何完全平局均以预登记稳定 ID 字典序解决；
6. 上述算法只使用 pre-lockbox development，最终产出恰好一个待检 candidate process，并冻结其 family/selector、arms、校准、q75 与年度重估算法。

### 14.5 单次 mechanical lockbox 与冠军数量

锁箱只运行 14.4 产出的唯一 candidate；其他 candidate 的 lockbox 预测和结果不得生成或揭示。它必须同时满足：

- T2 Brier skill 点估计大于 0，且同一防御分数对 T1/T3 的方向性诊断不翻转；
- 10bps 后相对同平均暴露控制的 terminal timing value 点估计大于 0；
- 上述 Brier loss improvement 与逐周相对控制 log-return 各自采用固定 13 周 moving-block bootstrap，其预登记 90% 下界均大于 0。

其中“方向性诊断不翻转”机器定义为：lockbox 内 Spearman `IC(raw_defense_score, T1) < 0` 且 `IC(raw_defense_score, T3) < 0`，同时 defense-score Q5 相对 Q1 的现金胜出率更高、平均 T1 更低、平均 T3 更低。

任一点估计不大于零即 `failed/no_transfer`；点估计为正但任一 90% 下界不大于零即 `inconclusive/no_transfer`。全部通过后，该 candidate 才成为唯一 T2 champion 并可授权 R2D。失败不得运行 runner-up、改阈值、增加模型或把 T1/T3 变成替补赢家。

## 15. 攻防归因

除 CAGR、Sharpe、MDD、Calmar、beta、换手和成本外，每条动态路径都直接分解相对满仓 SPY 的机会：

```text
defense_benefit = sum((1 - a_t) * max(-x_t, 0))
missed_upside   = sum((1 - a_t) * max( x_t, 0))
net_timing      = defense_benefit - missed_upside - incremental_cost
```

其中 `x_t` 为 SPY 相对 RF 的同期收益，`a_t` 为执行后实际风险权重。报告：

- `defense_benefit`、`missed_upside`、二者比值与 `net_timing`；
- upside/downside capture；
- 告警开始、解除、持续期与反复切换；
- 从局部低点到恢复 100% SPY 的 lag；
- 额外 L1 turnover 和成本；
- 危机下跌与随后反弹分别贡献多少。

该分解是以简单收益相加的机制代理，不与复合终点 active wealth 自动相等。报告必须另给 `compounding_reconciliation_residual = log(dynamic_wealth / full_spy_wealth) - additive_proxy`，并以实际复合财富作正式经济判定。一个策略若只把 `defense_benefit` 和 `missed_upside` 同时压到接近零，不能称为成功识别攻防时点。

## 16. R2D：迁移到 `mom_255_0`

R2D 只能读取 R2C 已落盘、不可变的逐周 OOS 分数、阈值、状态与目标仓位；不得在看到动量结果后重训、换 champion 或改阈值。

迁移边界：

- 横截面信号固定为 `mom_255_0`；
- Top10/20/50 全部保留；
- 股票选择频率保留 weekly/monthly，共 6 条裸策略路径；
- 防御 overlay 每周执行，只按比例缩放当前持股与现金，不更换证券名单；
- 主要对照为对应裸 `mom_255_0`、同平均暴露静态控制与 SPY；
- 成本按股票实际成交额重放，不能沿用单一 SPY ETF 的成本金额。
- 六条 R2D 路径与其裸/静态控制统一按实际 risky-asset L1 使用 10bps 主成本，0/5/20bps 为固定压力；由于 overlay 每周可交易，不沿用 Round 1 月频路径的 5bps 主成本。

共同样本与成交口径冻结为：

1. `R2D_overlay_update_dates = immutable_R2C_OOS_weekly_signals ∩ PIT_valid_dates ∩ mom255_complete_dates`；其中 `mom255_complete` 要求当日 PIT 资格、255-session 动量端点、下一执行 open 与终止事件记录均合格；
2. 绩效日历不是上述稀疏日期的压缩。Top10/20/50 × weekly/monthly 六条路径共用同一个连续 `evaluation_start_open` 与 `evaluation_end_close`，包含全部中间 sessions；overlay 缺分数周 carry-forward 上次权重且不产生 overlay 交易，裸策略 weekly/monthly 调仓仍按原日历执行；
3. 月频若需在共同起点前预滚持仓，预滚期不计绩效但完整审计；六条路径不得各自向后滑动起点；
4. 同一执行开盘按固定顺序处理：pre-open 冻结 terminal corporate action；若是裸策略调仓周则生成新的 TopK 等权 risky-book，否则保留当前 risky-book 相对组成；再乘 immutable overlay 权重；最后相对开盘前漂移持仓求一次净 target vector；
5. 成本只按这一个净向量的 `sum_i(abs(w_target_i - w_pre_i))` 收取一次；现金不进 L1。overlay 本身不换名单，但裸策略计划调仓仍可换名单，完全换仓时 risky-asset L1 可达到 2；
6. 股票信号、执行估值与 NAV 沿用 total-return-adjusted OHLC；普通分红/拆股不得再次过账，只有未被 TR 路径覆盖的冻结 terminal event 可显式处理，且必须有 no-double-count QA；
7. R2D 只能使用已经存在的 R2C OOS 周分数，不得为了填补更早 PIT 历史生成 in-sample 或 backfilled score。

R2D 的正式历史判定只使用 champion 在冻结后未参与 family/arm 选择的 mechanical-lockbox 周，以及其后按同一冻结算法生成的 forward 周；pre-lockbox OOS overlay 只作 selection-contaminated 描述性归因，不能满足 H4。即使 PIT 历史扩长，当前历史结果仍只能称为 `development transfer`，因为市场时期本身已被研究过程观察；纯净确认仍需未来 paper/live 或从未参与研究决策的外部市场。

H4 不挑单一路径，采用六路径 family gate。在 10bps 主成本下，至少 4/6，且 weekly 与 monthly 各至少 2/3 路径，必须同时满足：`wealth_overlay / wealth_naked - 1 > 0`、相对同平均暴露静态控制的 terminal timing value 为正、`Sharpe_overlay - Sharpe_naked > 0`、`MDD_overlay - MDD_naked > 0`（MDD 为负数，故正差表示回撤改善）；四项的六路径中位数也都必须大于零。六条路径必须全部完成，否则 R2D invalid；20bps 压力完整报告但不产生替补赢家。

若 PIT 股票、永久标识和公司行动没有覆盖 R2D 的共同 lockbox/forward 日历，R2D 记为 `insufficient_data/no_transfer`。优先目标是随 R2A 一并扩展可靠 PIT 数据，但数据变长不会自动把已经观察过的时期升级为独立确认。

R2D 完成后才讨论防御动作优化，例如 100/75/50/0 多级仓位、核心 + 战术 sleeve、债券久期或非对称再入场；这些不是 R2C 的模型选择参数。

## 17. 不可变产物与审计

每次正式运行至少产生：

- 数据与 QA manifest；
- 决策日历、特征、目标及 `available_at`；
- inner/outer fold 定义与 purge 记录；
- 每个 trial arm、family selector、outer candidate process、paired control 的 resolved config 与完整 ledger；
- 逐周 OOS raw score、calibrated probability、阈值、状态和目标仓位；
- trades、costs、daily NAV、exposure 与静态控制；
- IC、校准、分位、tail、年度、危机和归因报告；
- 数据、代码、依赖、配置、随机种子和所有产物 SHA256。

相同数据和配置必须产生相同预测哈希。成本情景只能重算交易与 NAV，不得改变特征、模型预测、状态或目标仓位。原 run 不覆盖；方法或数据错误按台账标记并创建新版本。

## 18. 与旧治理文档的关系

- [系统化实验计划 v2](./21_systematic_experiment_program_v2.md) 中“暂不使用机器学习”适用于已完成的短样本九宫格；本计划是在扩长数据、限制模型容量和建立 nested walk-forward 后启动的独立后续程序，不追溯修改 v2。
- [研究总计划](./00_research_plan.md) 原有“逐期 walk-forward”原则继续有效；Round 2 作一项前瞻、仅限本程序的 scope amendment：在 expanding walk-forward 之外增加按完整执行年度冻结的 mechanical lockbox。它不采用随机切分，且因 2018–2026 已被观察，只是选择防火墙而非纯净外部确认。
- [第一轮总结](./22_round1_main_grid_synthesis.md) 曾把 XS01 作为优先候选。该历史建议保留；用户随后决定先分离研究防御时点，XS01 因此延后，不取消。
- 当前免费 v3 及全部 G 组 bundle 保持不可变，第二轮创建新数据和新命名空间。

## 19. 第一版默认值与冻结前检查

第一版先采用以下默认值，不再阻塞计划书：

| 项目 | v1 默认 |
|---|---|
| 核心数据起点 | SPY 可交易起点 1993-01-29，经源核验 |
| 决策频率 | 每周最后交易日收盘 |
| 执行 | 下一 XNYS session 开盘 |
| 三个 target | T1 1周超额收益；T2 现金胜出；T3 4周最差超额路径 |
| 信号筛选仓位 | risk-on 100% SPY；defense 50% SPY/50% RF |
| 告警预算 | 训练历史防御分数 q75；全年冻结 |
| 主成本 | 10bps / SPY 实际买卖美元；0/5/20bps 压力 |
| 模型预算 | 17 个 long-core T2 trial arms → 最多 9 条 core outer processes；另有至多 1 个 `PIT01` paired bundle |
| 主 boosting | 受限 XGBoost |
| RF | 一个冻结配置的敏感性 challenger |
| 状态模型 | 两状态 HMM，仅 filtered probability |
| 深度学习 | 不开放 |
| 最终横截面信号 | `mom_255_0`，Top10/20/50 × weekly/monthly |

分阶段冻结清单：

1. R2A 前确认数据供应商、许可、PIT 历史起点、准确字段、adjustment/volume policy 与数据 QA，并冻结独立数据 design；
2. R2A 数据冻结后生成唯一 decision calendar、folds 与绝对 lockbox 日期；
3. 把所有特征公式、GAM 自由度、Ridge/XGBoost arms、HMM 数值门和概率校准写入 machine config；
4. 登记 17 个 long-core arm ID、至多 9 个 core outer selector/process ID、条件 `PIT01` bundle/treatment ID，以及所有不可晋级 paired-control ID，之后不得追加或替补；
5. 记录 2018–2026 已被观察的研究选择边界和 Round 2 lockbox scope amendment；
6. 由只读审计确认无未来数据、无同日不可成交收益、无跨折拟合，且锁箱尚未被任何候选揭示。

R2A 数据 design 与 R2B/R2C machine preregistration 分两次冻结；不能等待 folds 已存在后才反向授权数据构建。当前 planning 版本不授权下载付费数据、修改冻结 v3、计算 target/model 表现或产生正式结果。

## 20. 方法参考

- Random Forest 与 bagging：Breiman, [Random Forests](https://doi.org/10.1023/A:1010933404324)。
- Gradient boosting：Friedman, [Greedy Function Approximation](https://doi.org/10.1214/aos/1013203451)。
- 正则化 boosting 实现：Chen & Guestrin, [XGBoost](https://doi.org/10.1145/2939672.2939785)。
- 大规模 GBDT 的工程边界：Ke et al., [LightGBM](https://papers.nips.cc/paper/2017/hash/6449f44a102fde848669bdd9eb6b76fa-Abstract.html)。
- 状态转换：Hamilton, [A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle](https://ideas.repec.org/a/ecm/emetrp/v57y1989i2p357-84.html)。
- 金融机器学习的数据规模对照：Gu, Kelly & Xiu, [Empirical Asset Pricing via Machine Learning](https://academic.oup.com/rfs/article/33/5/2223/5758276)。
- 简单线性时序基准：Zeng et al., [Are Transformers Effective for Time Series Forecasting?](https://ojs.aaai.org/index.php/AAAI/article/view/26317)。
- 多模型选择偏差：White, [A Reality Check for Data Snooping](https://doi.org/10.1111/1468-0262.00152)；Hansen, [A Test for Superior Predictive Ability](https://doi.org/10.1198/073500105000000063)。
