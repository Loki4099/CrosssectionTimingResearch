# Round 3 v1 amendment 1：development 危机窗可用性修正

记录日期：2026-08-16  
状态：**结果前冻结。** 尚未实现 runner、生成状态、运行回测或创建 bundle。

原计划第 8 节 H4 和 `config/experiments/round3/program.toml` 把 dot-com、GFC、COVID selloff 都列为 development leave-one-crisis-out 窗口。但 development 经济路径从 2005 年首个执行开盘开始，dot-com 窗口在评价起点之前，删除它不会删除任何 session，也不构成独立稳健性检查。

因此作唯一修正：

- H4 的逐危机删除只包含 GFC 与 COVID selloff；
- dot-com 日期仍可作为历史数据标签保留，但不参与 development gate；
- 其他公式、阈值、状态机、成本、锁箱和停止规则不变。

本修正由日期可用性审计触发，不使用任何 R3A 策略结果。

