# Round 5：连续 MAE13 单因子复审计划 v1

状态：`preregistered_development_authorized`
计划编号：`defense_mae13_single_factor_round5_v1`
证据等级：development / free-data / `formal_eligible=false`

## 1. 研究问题

Round 4 证明下一周 `SPY < cash` 的二分类标签会把轻微下跌与暴跌等权，并与中期不利路径明显错位。Round 5 只改变评价 target，不改变17条合格因子的公式、方向、窗口、数据或统一仓位测量尺。

本轮问题是：单个因子能否稳定排序“从下一可成交开盘开始，未来13周内超过5%容忍范围的 SPY 相对现金最大不利路径”？本轮不研究多因子、模型、`mom_255_0`、债券替代、仓位搜索或锁箱。

## 2. 时间与锁箱

- 因子信号：每周最后一个 XNYS session 收盘；
- 执行起点：下一 XNYS session 开盘；
- target 路径：起点开盘后至第13个计划执行开盘，包含期间每个 session 的总回报收盘；
- development execution years：2005–2021；
- 最后成熟 target signal：2021-09-24；
- 策略代理最后 signal：2021-12-23，NAV 截止2021-12-31；
- 锁箱：signal 2021-12-31 / execution 2022-01-03 起，所有 target、预测、状态、NAV与指标禁止生成。

2022–2026 是机械选择防火墙，不宣称纯净外部 OOS。只有未来唯一完整 pipeline 冻结后才可单次解封。

## 3. 正式 target

令 `e_t` 为下一执行开盘。对13周路径内每个日收盘及终点执行开盘，计算 SPY 总回报财富相对同期现金财富的累计对数比 `X_t(u)`。定义：

`M_t = max(0, -min_u X_t(u))`

`raw_MAE13_t = 1 - exp(-M_t)`

唯一正式 target：

`Y5_t = max(raw_MAE13_t - 0.05, 0)`

原始 `raw_MAE13` 与 `Y10=max(raw_MAE13-0.10,0)` 只作描述性敏感性，无权选因子、改门槛或救回失败结果。该标签从当前决策点锚定，不是未来任意峰谷 MDD，也不是13周期末收益。

## 4. 冻结因子

因子集合以 `config/experiments/round5/factor_registry.csv` 为唯一机器目录，共17条。全部沿用 R4A 冻结值，分数越高越防御。禁止增加替补、改变窗口、反转方向、标准化搜索或组合信号。

## 5. 批次

1. `R5A_MAE13_TARGET`：只生成 target、可用性、分布与因果 QA；
2. `R5B_MAE13_SINGLE_FACTOR`：17条单因子的 raw-score 连续 target 评价；
3. `R5C_SPY_CASH_PROXY`：统一 q75、100/50 SPY/cash、10bp 主成本经济测量；
4. `R5D_MAE13_ROBUSTNESS`：共同样本、13周 block、BH-FDR、年度与 major-event leave-one-out。

各批全部完成后硬停止，不因中间阳性或阴性提前改计划。

## 6. 信号评价

主统计量是预定向 defense score 与 `Y5` 的单侧时间序列 Spearman。辅项为：

- defense-score 五分位的 `Y5` 单调性；
- top-25% score 对 `sum(Y5)` 的捕获率与相对25%随机预算的 lift；
- top-25% 与其余75%的均值/中位数差；
- `raw_MAE13>=5%/10%` 的 precision、recall、lift；
- `alert & Y5=0` 误报率；
- 完整执行年度方向稳定性。

13周标签高度重叠，禁止 iid t-test。主推断使用13周 moving-block bootstrap，17因子统一做 Benjamini–Hochberg FDR，`q<=0.10`。

## 7. 统一经济代理

每个因子按执行年度冻结历史 q75，历史至少260个有效周；`score > q75` 时目标50% SPY，否则100% SPY。缺失周沿用上一状态且不产生 overlay 交易。每周下一开盘恢复目标；现金按冻结 RF 计息。

- 主成本：10bp/每美元 SPY 实际交易额；
- 敏感性：0/5/20bp；
- 主对照：同日历、同成本、同平均股票暴露的静态 SPY/cash replay；
- 经济量：active terminal wealth、CAGR、Sharpe、MDD、换手、逐年主动贡献。

策略代理只是测量尺；单因子不能仅凭降低平均暴露获得择时阳性。

## 8. 判定

`reference_positive` 同时要求：Spearman > 0、top-25% loss-capture > 25%、top组平均 `Y5` 高于其余组、10bp相对同暴露终值 > 0、至少60%完整年度主动贡献 > 0。

`robust_reference_positive` 进一步要求：13周 block 单侧下界 > 0、BH-FDR q<=0.10、捕获率至少35%、top组平均 `Y5` 至少为其余组1.25倍、native与共同样本同向、逐一剔除主要回撤事件后方向不反转、10bp主动财富仍为正且动态 MDD 优于 always-SPY。

若无因子通过，结论是“当前17条单因子对 MAE13 无稳健排序能力”；不得扫描2/4/6% dead-zone、4/26周 horizon、q50/q90或新窗口救参。

## 9. 输出与停止

每个 runtime bundle 必须不可变、带 manifest、输入锁哈希、文件字节与 SHA256、`lockbox_read=false`。完成报告与精简发布后状态改为 `completed_pending_user_factor_combination_decision`。多因子或模型必须由用户另行决定并新建预注册。
