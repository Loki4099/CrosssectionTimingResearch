# Round 10：P00 × mom_255_0 机械锁箱计划 v1

Round 10 检验 Round 9 冻结的端到端候选在 2022-01-03 至 2026-06-30 的机械锁箱表现。由于 G00 横截面历史此前已被观察，本轮只称 mechanical firewall evidence，不称纯前瞻或正式外部 OOS。

计划分三道不可逆边界：R10A 只扩展同源 Tiingo RSP 并验证其与 2003–2021 冻结快照重叠区逐值一致；R10B 只按时间顺序生成 P00 状态和六格下一开盘目标账簿，不输出 NAV、收益或绩效；R10B 账簿哈希冻结并单独验收后，R10C 才能读取执行收益并揭示结果。

最终 primary 保持 `mom_255_0 / Top20 / monthly / long-only / equal weight / 10bp`。Top10/20/50 × weekly/monthly 六格、0/5/10/20bp、1.0/0.5暴露、联合事件顺序、一次股票L1收费、matched-static定义和 Round 9 的所有门槛均不得改变。

P00 的 raw risk score 仍为 `-Δ63 log(RSP/SPY)`。2021年第四季度桥接使用冻结的2021 outer threshold；2022起每年使用与Round 7一致的 expanding q75：在当年第一条信号前排除13个计划周，再对全部历史raw score计算线性q75。阈值只依赖截至当时已经发生的factor值，不读取Y5或策略收益。风险优先、至少一周防守、风险解除即恢复和1.0/0.5权重均原样冻结。

R10A之后必须硬停并建立数据验收；R10B之后必须再次硬停并把完整状态、目标、代码、依赖和manifest哈希写入outcome-reveal lock。任何阶段发生哈希变化，后续证据全部invalid。
