# R3B v1 amendment 1：跨锁箱四周标签防火墙

记录日期：2026-08-16  
状态：**结果前冻结。** 尚未运行真实 R3B walk-forward、状态机或经济回测。

原计划要求 2021-12-31 signal 及以后标签为空，但四周标签还可能由更早 signal 开始、在 2022-01-03 及以后结束。为避免通过跨界标签读取 mechanical lockbox outcome，冻结更严格规则：

```text
withheld_lockbox = (signal_session >= 2021-12-31)
                   or (next_4w_execution >= 2022-01-03)
```

被 withheld 的连续和二分类标签均保持空白，不参与训练、评分或 gate。Development outer 预测仍可生成至 2021-12-23，但跨界预测没有 outcome 评价。

本修正由日期边界审计触发，不使用任何 R3B 结果；其余规格不变。

