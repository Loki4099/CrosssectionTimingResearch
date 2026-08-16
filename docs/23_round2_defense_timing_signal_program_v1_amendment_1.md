# Round 2 v1 前瞻修订 1：锁箱 outcome 防火墙

日期：2026-08-16  
状态：**在生成任何 R2B target、模型分数或策略结果前冻结。**

本修订只消除主计划在实际冻结日历落地后出现的边界冲突，不改变特征、模型、targets、仓位、成本、trial 预算或胜出门槛。

实际 R2A 日历机械确定：development outer 为 2005–2021；mechanical lockbox 从 2021-12-31 signal / 2022-01-03 execution 开始。因此：

1. development 危机留一门只使用其区间内的 dot-com、GFC 与 COVID selloff；COVID rebound 仍为 development 再进攻诊断。
2. 2022 bear 完全位于 lockbox，不参与 development arm/family/champion 选择，也不生成 candidate-independent 诊断。
3. R2B 可为全部日期生成不含 outcome 的 features，但 `targets_weekly` 对 lockbox 周只保存日期、成熟时点规则和 `withheld_lockbox=true`；T1/T2/T3 值必须为空。pre-lockbox signal 的某个 target 若实际 `target_available_at` 晚于 2021-12-31 signal close，该 target 也必须单独留空；T1/T2 与 T3 使用各自成熟门，不能为了保留年末周而偷看跨入锁箱的 outcome。
4. development 选出并冻结唯一 provisional candidate 后，lockbox runner 先从已有 feature 与冻结历史训练数据生成该 candidate 的全部预测并锁定 prediction hash，随后才在同一不可变运行中计算/解封 lockbox targets、评分与 2022 诊断。
5. 任何其他 candidate 的 lockbox prediction、target-joined score 或经济路径均不得生成。锁箱失败不得回看 runner-up。

这使主计划“最后不少于208周的完整年度锁箱”“锁箱只运行一次唯一 candidate”与“危机窗口预登记”同时成立；不得通过把2022移回development、缩短锁箱或先生成隐藏但可读取的 target 文件规避防火墙。
