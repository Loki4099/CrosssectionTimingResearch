# R5A MAE13 target：冻结设计

只读入 R4A/R2A 已冻结的 SPY TR、RF与决策日历。生成每周起点开盘至未来13周日收盘与终点开盘的相对现金路径，正式值为 `Y5=max(raw_MAE13-5%,0)`。起点到signal隔夜不计入，终点必须早于2022-01-03；路径任一价格或RF缺失即整行不可用，不插值。

输出至少包含 `week_id/signal/execution/terminal_execution/target_available/raw_mae13/y5/y10/target_available_at`。报告零值比例、分位数、年度分布、严重度集中与相邻周持续性。不得读取因子表现、生成策略或锁箱 outcome。
