# R2C_SPY_TBILL：受限模型与统一经济代理——冻结设计

状态：**设计冻结；R2B 合格前不得运行。** 本批只使用 R2B 不可变 features/development-targets、[固定 folds](../../../config/experiments/round2/folds.json)和[锁箱 outcome 防火墙修订](../../23_round2_defense_timing_signal_program_v1_amendment_1.md)，严格执行 development 选模 → 单一 candidate 冻结 → 单次 lockbox 的顺序。

## 1. 研究对象与禁止事项

- T2 `cash_wins_1w` 是唯一拟合和选模目标；T1/T3 不另拟合模型，只评价同一 OOS score。
- 免费 L 线 core 是唯一输入。F3、F4、F5、PIT01 全部不开放，也不允许替补特征。
- 17 个 trial arms 和最多 9 条 outer processes 为总预算；失败、未开放和数值错误都计入 ledger。
- 禁止 LightGBM/CatBoost、任意特征子集、ensemble、seed 竞赛、阈值/仓位搜索、深度学习和查看 runner-up 的 lockbox。

## 2. 数据分段与锁箱防火墙

- development outer：2005–2021，共 17 个完整执行年度；年度边界携带持仓和 NAV，不重置。
- mechanical lockbox：2021-12-31 signal / 2022-01-03 open 至 2026-06-26 signal / 2026-06-29 open，共 235 周；它是单独证据段，只从全现金启动一次。
- 所有候选只可在 development 生成 OOS 结果。14.4 算法冻结唯一 candidate manifest 后，锁箱 runner 才接受该 candidate ID/hash；其他 candidate 的 lockbox 预测不得生成。
- 每个 outer 年在映射至当年首个 execution open 的 signal close 之前冻结 family/arm selector、transform、Platt 与 q75 算法。
- R2B 的 lockbox T1/T2/T3 必须为空。唯一 candidate 的 lockbox predictions 先写入并哈希锁定，之后同一 runner 才计算并连接 lockbox target；禁止先生成一个所有模型都能读取的 lockbox target 表。
- 边界统一排除 test/validation 前 5 个 scheduled signals，最后允许训练索引为 `v-6`；实际日期由 folds.json 决定。

## 3. 17 个 trial arms

### 3.1 简单阶段（11 arms）

- 四条 sentinel：`SENT_RV21`、`SENT_SMA_GAP`、`SENT_DRAWDOWN252`、`SENT_RET21`。
- Ridge Logistic：L2 `lambda ∈ {0.01,0.1,1,10}`，截距不惩罚；实现若使用 inverse-C，必须严格映射 `C=1/lambda`。
- Additive Logistic GAM：每项 feature 独立 `SplineTransformer(n_knots=3, degree=2, knots='quantile', extrapolation='linear', include_bias=false)`，不含交互；随后 L2 logistic，`lambda ∈ {0.1,1,10}`。每项最多 4 个 spline basis；knots 仅在训练窗拟合。

### 3.2 条件复杂阶段（6 arms）

仅当至少一个 simple outer process 同时通过信号和经济硬门才开放：

- Random Forest：1000 trees、depth 3、`min_samples_leaf=0.10`、`max_features='sqrt'`、bootstrap、seed 20260816、无 class weight；OOB 不选模。
- XGBoost：depth `{1,2}` × trees `{25,75}`；learning rate .05、L2=1、subsample/colsample=1、exact/depthwise、seed 20260816；每折 `min_child_weight=.025*n_train`，并审计实际叶样本数 `>=ceil(.10*n_train)`。
- HMM：2-state diagonal Gaussian；观测为当周 signal-close SPY log return、`log_spy_rv126`、`log_rv21_over_rv126`；10 个固定 restart seeds 0..9、max_iter 500、tol 1e-6、covariance floor 1e-6。风险态按 train 内 `log_spy_rv126` 条件均值较高者固定；使用 `pi_t @ A` 的 one-step 风险态概率，禁止 smoothed state。

## 4. Inner selector 与概率校准

每个 outer 年只使用 folds.json 中最近至多 5 个 52 周 expanding validation blocks；少于 3 个则 process invalid。Ridge/GAM/XGB 只在各自登记 arms 中，以第 2 块以后的 prequential-calibrated Brier loss 选择；13 周 moving-block paired SE 的 one-standard-error set 内按以下容量顺序取最简单：

```text
Ridge: lambda 10 > 1 > .1 > .01
GAM:   lambda 10 > 1 > .1
XGB:   d1n25 < d1n75 < d2n25 < d2n75
```

每条 candidate 保存 `raw_defense_score` 与 `p_cash_wins`。唯一校准器为无惩罚 sigmoid/Platt `sigmoid(a+b*z)`，只用更早 inner-OOF score/T2；`b<=0`、单类或不收敛即 invalid，禁止 isotonic。年度最终 calibrator 可用该 arm 全部已完成 inner-OOF records 拟合，但不得用 outer 标签。

年度 q75 只由该 process 的 inner/prequential OOS raw score 计算，全年冻结；unique score 不足或完整 outer 年告警率 0%/100% 即 invalid。

## 5. 统一经济代理

```text
raw_defense_score < train_q75 : 100% SPY / 0% RF
raw_defense_score >= train_q75:  50% SPY / 50% RF
```

下一 XNYS session 开盘执行，每周恢复目标。旧持仓先按开盘估值，`turnover=abs(w_target-w_pre)`，`cost=pretrade_NAV*cost_rate*turnover`；现金不进 L1。主成本 10bps，压力 0/5/20bps。开盘后的现金获得当日 RF。

每条动态路径完整 replay 同平均实际暴露和同实现超额波动两个静态控制；权重限定 `[0,1]`，无解不得加杠杆。development 与 lockbox 各自估计其证据段的 ex-post control weight。主量为 `dynamic_wealth/static_wealth-1`，不做 NAV 相减。

## 6. Development 选择与停止

信号硬门：aggregate Brier skill>0；T1/T3 Spearman 均<0；score Q5 相对Q1现金胜率更高且 T1/T3 均值更低；校准斜率>0；整体告警率 `[5%,50%]` 且每完整年非0/100%。

经济硬门：10bps 后相对同平均暴露终值>0；完整年度至少 60% 为正；相对同波动控制终值>=0；正年度贡献最大占比<=50%；逐一删除 development 内的 dot-com、GFC、COVID selloff 后仍>0。2022 bear 只属于单次 lockbox 诊断，不参加 development 选择。

先在 sentinel/Ridge/GAM 中按 Brier + 固定 13 周 paired one-SE + `sentinel<Ridge<GAM` 得到 simple benchmark。无 simple benchmark 则复杂阶段不开放并停止。复杂 process 还须改善至少 1 paired SE，校准截距/斜率/ECE不劣、两个 timing controls 不劣、逐危机删除后 Brier 改善仍>0。最终按 `sentinel<Ridge<GAM<HMM<RF<XGB` 的同一 one-SE 规则冻结唯一 provisional candidate。

## 7. 单次 lockbox

只有 provisional candidate 可运行 lockbox。通过条件同时为：Brier skill>0；T1/T3 方向与Q1/Q5序不翻转；10bps 后同平均暴露 timing value>0；Brier improvement 与相对控制周度 log-return 的 13 周 moving-block bootstrap 90% 下界均>0。

点估计失败为 `failed/no_transfer`；点估计为正但区间未过为 `inconclusive/no_transfer`；全部通过才成为唯一 champion。失败后禁止 runner-up、改阈值、追加模型或用T1/T3替补。

## 8. 不可变输出

Development bundle 至少包含 arms/selector ledger、inner/outer predictions、calibration、scores、NAV/controls、annual/crisis diagnostics、resolved configs 与 manifest。其 manifest 明确 `lockbox_predictions_present=false`。随后单独的 candidate-freeze manifest 锚定唯一 process。

Lockbox bundle 只包含该 candidate 的预测、诊断、NAV/controls 与判定，manifest 必须证明其他 candidate ID 不存在。所有 bundle 已存在即拒绝覆盖，并记录 R2A、R2B、folds、program/design/config、代码与依赖 SHA。
