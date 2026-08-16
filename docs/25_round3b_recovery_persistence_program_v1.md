# R3B：恢复持续性确认计划 v1

状态：**development 机器预注册已冻结；锁箱与动量迁移未授权。**

## 1. 前序停止条件

R3A 已证明“两日站上 SMA21 就恢复满仓”不是合格出口：它减少错失上涨，却损失更多防守收益，只保留 5.43% 的对称 RV21 回撤改善。R3B 不调整 SMA 长度、确认天数、RV 参数或防守仓位；只增加一个独立的恢复持续性确认，检验价格反弹是否有未来四周正超额收益支持。

这是新的 development 预注册，不得读取 R3A/R2C mechanical lockbox outcome，也不得用失败后替换模型或阈值。

## 2. 唯一目标

对周末信号 `s_t`，下一 XNYS session 开盘为 `e_t`，第四周计划执行开盘为 `e_(t+4)`：

```text
fwd_excess_logret_4w = log(SPY_TR_open[e_(t+4)] / SPY_TR_open[e_t])
                       - sum_{d in [e_t,e_(t+4))} log(1+RF_d)
sustainable_attack_4w = 1[fwd_excess_logret_4w > 0]
```

等于 0 归非持续；成本不进入标签。标签在 `e_(t+4)` 开盘及对应 RF 全部可用后才成熟。2021-12-31 signal 及以后标签必须保持空白。

`fwd_worst_excess_4w` 只作否决诊断，不另拟合模型。

## 3. 唯一模型

固定四项周特征，全部截至 `s_t` 收盘：

1. `spy_total_return_21d`；
2. `sma50_over_sma200_minus_1`；
3. `drawdown_from_252d_high`；
4. `log_rv21_over_rv126`。

每个执行年度只拟合一条 L2 Ridge Logistic：`C=1`、截距不惩罚、LBFGS、max_iter=2000、无 class weight、无概率校准。训练窗内以 1%/99% winsor、median imputation、sample-std 标准化；所有变换只在训练窗拟合。

禁止更换特征、子集选择、λ 网格、GAM、RF、XGB、HMM、ensemble、seed/阈值竞赛和深度模型。

## 4. Walk-forward 与防火墙

- 复用 `config/experiments/round2/folds.json` 的 17 个 development outer 年 2005–2021，共 887 周。
- 每年只用该 fold 的 `train_start_signal..train_end_signal`；现有 `train_end_signal` 已在 test 前排除 5 个 scheduled signals，足以覆盖四周标签成熟和一周 embargo。
- 年度 pipeline 在映射到该年首个 execution open 的 signal close 前冻结。
- 每个 outer 年必须至少 520 条 feature-complete、label-mature rows；标签单类、模型不收敛或特征退化则 candidate invalid，不现场救参。
- development 预测最大 signal 必须为 2021-12-23；任何 2021-12-31 及以后 target、prediction、state、NAV 或 metric 都是 fatal leakage。

## 5. 唯一退出规则

进入与滞回完全复用 R3A：

- `FULL_ARMED` 中 `RV21 > strict lagged 756-session q75`，下一开盘进入 50% SPY 防守；
- `RECOVERY_UNARMED` 必须先观察 `RV21<=q75` 才 re-arm；
- 同一高波 episode 内禁止再次进入。

唯一变化发生在 `DEFENSE`：

```text
price_recovery = two consecutive closes strictly above causal SMA21
model_recovery = p(sustainable_attack_4w) > outer_train_base_rate
exit_defense = price_recovery and model_recovery
```

严格大于；等号不退出。若年度模型 invalid 或 score 缺失则维持 50% 防守。进入防守优先，最少持有一周。目标仍为 100% 或 50% SPY，无做空、无杠杆。

## 6. 比较路径

同一会计和成本下比较：

- `ALWAYS_SPY`；
- `SYMMETRIC_RV21`；
- `R3A_PRICE_ONLY`；
- `R3B_PERSISTENCE_CONFIRMED`。

R3A 路径必须逐位等于其冻结 bundle 的 887 条状态和 10bp NAV；不允许重新解释历史规则。R3B 另配同平均暴露和同实现波动静态控制。主成本 10bp，压力 0/5/20bp。

## 7. Development 硬门

所有门必须同时通过：

### H1：模型确有恢复持续性信息

- pooled outer Brier skill 相对逐年训练 base rate >0；
- pooled ROC-AUC >0.5；
- `p_attack` 与四周超额收益 Spearman >0；
- 完整 outer 年至少 60% Brier improvement >0；
- outer test 的 attack rate 每年均在 `[5%,95%]`，不得退化。

### H2：优于两个既有出口

- 10bp 下相对 `SYMMETRIC_RV21` 和 `R3A_PRICE_ONLY` 的终值均 >0；
- 相对对称规则 CAGR 改善 >0；
- 相对对称规则错失上涨至少减少 25%，同时防守收益至少保留 75%；
- 相对对称规则的年度 active contribution 至少 60% 为正。

### H3：不是只改变 beta

- 相对同平均暴露和同波动控制终值均 >=0，且前者严格 >0；
- `net_timing>0`、`defense_benefit/missed_upside>1`；
- MDD 仍优于 always-SPY，并至少保留对称规则 MDD 改善的 75%。

### H4：稳健性

- 正年度贡献集中度 <=50%；
- 分别删除 GFC、COVID selloff 后同平均暴露 timing value 仍 >0；
- 截止 2021-06-30 的 timing value >0；
- 0/5/20bp 三档相对对称规则终值方向不翻负。

任一失败即 `completed_no_persistence_candidate`，锁箱与 `mom_255_0` 迁移继续关闭；禁止改阈值、提高 runner-up 或增加模型。

## 8. 锁箱与输出

只有 H1–H4 全通过才可冻结 candidate manifest 并单次运行 2022–2026 mechanical lockbox。即使通过，也只叫 adaptive/mechanical OOS。

Development bundle 至少包含 targets、annual model ledger、OOS predictions、weekly states、四路径 NAV、controls、mechanism、gate、resolved config 与 manifest；同 run-id 已存在即在读数据前拒绝覆盖。
