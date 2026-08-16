# R2C_SPY_TBILL：简单阶段开发期结果报告

状态：**已完成；没有 simple benchmark，复杂阶段、mechanical lockbox 与 R2D 均按预注册门禁停止。** 本报告只对应不可变运行 `r2c-simple-development-v1` 和 2005–2021 development outer。没有产生 2022–2026 的模型预测或 target，也没有运行 RF、XGBoost、HMM。

## 一句话结论

四条 sentinel、Ridge 和低自由度 additive GAM 没有任何一条同时通过信号质量与经济价值硬门。最根本的问题不是候选完全没有降低回撤：RV21 与 SMA50/200 的统一 100%/50% SPY 策略在开发期相对同平均暴露控制分别有 `+3.00%` 和 `+17.95%` 的累计择时增量，MDD 也明显较低；问题是它们无法形成跨年度完整、方向稳定的 `P(cash wins next week)` 概率路径，高风险分数与下一周收益的关系反而多为正，且收益高度依赖 GFC/COVID 等个别时期。Ridge/GAM 的完整概率路径同样失败，成本后相对两个静态控制均为负。

因此 `complex_gate_open=false`。本轮没有资格用 RF/XGBoost/HMM 救场，也没有 provisional candidate 可送入锁箱。这一结果再次支持：波动率/趋势变量能描述未来路径危险，却尚未稳定回答“下一周应该防守还是进攻”。

## 不可变运行与验收

- 运行锚：R2A manifest `071055016268d83f60a03b70be498d85da07897d290b049e4ed7524d1b9e674c`；R2B manifest `831cf4c4c373a762d7726833fcaebf80db0214c091a640d94955afa283058559`；folds `e0a18efcd533bd1e836cde4a8e9e9bc3dd0c343eb690b5a7ccc384093bf7c53c`；development authorization `dea2f2b472585c07c896cfcc12c9e94e8ed6b837b2fc20260cc20f075e895e47`。
- R2C manifest SHA256 `dafc0b31d97018e639a1886f47b6f923b4b96cdab706b39d3f2b195189870cd0`；完整 10-file tree SHA256 `84a6bc6d31dfac5b12ce0edfa3679447defaee95891b2feb10ceeb3554258a87`。
- clean build commit 为 `7de670b5299d5c4d9f54d0f390357b90b5a2ee5e`；依赖为 NumPy 2.3.5、pandas 3.0.1、PyArrow 25.0.1、scikit-learn 1.9.0、SciPy 1.18.0。
- 6 条 outer process 各有 887 个唯一周预测，共 5,322 行；日期为 2004-12-31 signal 至 2021-12-23 signal。共同有效 T1/T2/T3 评价周为每 process 883 行。
- 17 个完整 outer 年各有一条 selector 记录，共 102 行；arm ledger 为 187 行：4×17 sentinel、4×17 Ridge 和 3×17 GAM。
- 77,040 行日度 NAV 恰为 6 process × 3 source × 4,280 sessions；development 只在 2005-01-03 首次从现金启动，年度边界不清仓。
- 日收益由 NAV 独立复算最大误差为 0；同平均暴露控制的均值误差 `4.19e-13`，同实现超额波动控制误差 `4.57e-15`，终点 timing value 复算误差 `8.33e-17`。
- bundle 的 9 条非 manifest 文件记录逐 bytes/SHA 验收为零失败；同 run ID 重跑由 `FileExistsError` 拒绝。
- 最大预测信号为 2021-12-23。Manifest 明确 `lockbox_predictions_present=false`、`lockbox_targets_present=false`、`r2d_authorized=false`。

本结果仍为 `formal_eligible=false` 的免费本地研究证据。

## 概率与信号门

预注册要求每个 outer 年都使用当年开始前的 inner-OOF 分数拟合唯一 Platt 映射，且斜率必须严格为正。斜率非正不能靠翻转方向补救；该年度 candidate 记为 invalid。

| Process | 有效概率周 / 887 | 无效 outer 年 / 17 | 部分样本 Brier skill | T1 Spearman | T3 Spearman | 信号门 |
|---|---:|---:|---:|---:|---:|---|
| RV21 sentinel | 105 | 15 | +0.0025 | **+0.0679** | -0.1816 | 失败 |
| SMA gap sentinel | 52 | 16 | -0.0601 | +0.0259 | -0.0556 | 失败 |
| Drawdown252 sentinel | 0 | 17 | 不适用 | **+0.0958** | -0.1516 | 失败 |
| Ret21 sentinel | 157 | 14 | -0.0001 | +0.0369 | -0.0864 | 失败 |
| Ridge selector | 210 | 13 | -0.0649 | +0.0185 | **+0.0445** | 失败 |
| GAM selector | 522 | 7 | -0.0210 | +0.0032 | +0.0033 | 失败 |

“部分样本 Brier skill”只描述存在有效概率的年份；由于六条 process 的 `complete_probability_path` 全部为 false，它不能作为正式胜出证据。所有 process 的 T1 Spearman 也均为正，而预注册防御分数要求负值。Drawdown/RV/Ret21 对 T3 仍有正确的负相关，但 T3 只能否决 T2 candidate，不能替代 T2 产生 winner。

Ridge 的年度 selector 选择 L2 `lambda=10` 为 16/17、`lambda=1` 为 1/17；GAM 选择 `lambda=10` 为 13/17、`lambda=1/.1` 各 2/17。强正则被 one-SE 规则频繁选择，说明额外自由度没有表现出稳定增量。

## 统一 SPY/T-bill 经济代理

所有策略在年度训练分数 q75 以上持 50% SPY/50% RF，否则持 100% SPY；每周下一开盘恢复目标，主成本 10bps。静态控制以同一执行日历和成本完整 replay，不是简单按日线性拼接。

| Process | 动态 CAGR | 动态 Sharpe | 动态 MDD | 平均 SPY 暴露 | vs 同暴露 | vs 同波动 | 正年度 | 经济门 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| RV21 sentinel | 9.51% | 0.661 | -34.89% | 85.93% | **+3.00%** | **+29.02%** | 10/17 | 失败 |
| SMA gap sentinel | **10.64%** | **0.663** | -34.44% | 88.84% | **+17.95%** | **+34.10%** | 12/17 | 失败 |
| Drawdown252 sentinel | 9.16% | 0.611 | **-34.44%** | 88.63% | -5.84% | +16.02% | 9/17 | 失败 |
| Ret21 sentinel | 8.37% | 0.547 | -41.76% | 89.50% | -17.71% | -0.57% | 5/17 | 失败 |
| Ridge selector | 8.12% | 0.463 | -49.56% | 89.10% | -20.56% | -23.62% | 7/17 | 失败 |
| GAM selector | 8.61% | 0.494 | -45.39% | 88.62% | -13.67% | -15.55% | 7/17 | 失败 |

RV21 与 SMA 仍不能通过经济硬门：

- RV21 的正年度为 58.8%，低于 60%；去掉 GFC 或 COVID selloff 后，相对同暴露 timing value 分别变为 `-19.73% / -5.51%`。
- SMA 的正年度为 70.6%，但正年度贡献集中度为 52.8%，高于 50%；去掉 GFC 后 timing value 为 `-11.02%`。
- 2005 才开始 outer OOS，dot-com 窗口没有 OOS session（删除 0 日），因此该项只记录为不可评价，不被误述为通过危机稳健性。
- Drawdown 虽相对同波动控制为正，但相对同平均暴露为负；其余三条 process 对两个控制均没有稳定增量。

这组结果不能被概括成“简单均线策略已经成功”。它是值得保留的开发期机制线索：SMA/RV 的离散防御比第一轮纯波动率缩放更接近有效，但当前概率方向、危机集中和预注册完整性仍不合格。

## 预注册停止判定

1. 六条 simple process 的信号门全部失败；没有 `best_simple`。
2. 因 simple benchmark 不存在，RF、四条 XGBoost arm 与 HMM 统一登记 `not_opened_by_preregistered_gate`。
3. 没有 provisional candidate，故不生成 2022–2026 lockbox prediction/target，不运行 R2D `mom_255_0` transfer。
4. 不允许在本轮改 Platt 方向、改 q75、调整 50% 防御仓位、提升 SMA runner-up 或追加特征来救场。

如果后续继续，应先作为一项**新的、独立预注册研究**讨论“方向/尾部双头模型”或非对称防守—再入场状态机，而不是回开本轮锁箱。

