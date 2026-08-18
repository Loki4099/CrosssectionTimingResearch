# Round 9：P00 × mom_255_0 long-only 迁移决策备忘录

Round 9 已按冻结计划完成并在 R9C 硬停。结论很清楚：**P00 对 `mom_255_0` long-only 的开发期迁移通过**，而且不是只在唯一 primary 上偶然通过——六个 TopK × 频率格全部通过四项经济门。

唯一 primary `mom_255_0 / Top20 / monthly / long-only` 在 2018–2021、10bp 下，P00 overlay 终值为 **1.6505**，裸策略为 **1.3952**，即终值提高 **18.30%**；相对同平均实际股票暴露 static 的 timing value 为 **+24.16%**。CAGR 从 **8.68%** 提升至 **13.35%**，Sharpe 从 **0.442** 提升至 **0.750**，MDD 从 **-37.75%** 改善到 **-26.60%**。代价是累计 L1 turnover 从 33.86 增至 40.79。

成本压力没有改变方向：primary 在 0/5/10/20bp 下相对裸策略终值分别提高 19.12%/18.71%/18.30%/17.48%，相对 matched-static 的 timing value 分别为 +26.04%/+25.10%/+24.16%/+22.31%。2018、2019、2020 年主动贡献为正，2021 年轻微为负；13 周 moving-block 下界仍为正，单侧 p=0.0198。

六格在 10bp 下全部满足：overlay/naked 终值比大于1、timing value为正、Sharpe增量为正、MDD改善为正。六格中位数分别为终值增量 **+22.39%**、timing **+23.69%**、Sharpe **+0.302**、MDD **+16.09个百分点**；weekly 3/3、monthly 3/3。20bp 下六格 timing 均未翻负。对三个落在样本内的主要事件逐一剔除后，primary timing 最低仍为 **+16.81%**。

执行审计同样闭合：24条 naked/cost 身份检查全部通过，新联合事件 runner 对冻结 G00 的最大逐日 NAV 误差低于 `1e-11`；月频账簿在周度 P00 调整日没有重新选股，同一开盘只形成一个净股票目标并收一次实际股票 L1 成本。六格 matched-static 的实际日均股票暴露均与 dynamic 匹配到数值精度，约为73.3%。

因此，当前端到端 development candidate 可冻结为：`raw RSP/SPY63 → P00风险优先状态 → 1.0/0.5总暴露 → mom_255_0 Top20 monthly long-only`。其余五格只保留为可迁移性证据，不替补 primary，也不触发 TopK 或频率选择。

本轮仍不构成正式样本外确认：G00 横截面历史和相关开发区间已被研究观察，免费 PIT 数据也为 `formal_eligible=false`。2022–2026 未被 Round 9 runner 读取。程序状态是 `completed_pending_user_lockbox_decision`；机械锁箱必须另行授权，并先冻结 sealed prediction/target phase，不能根据锁箱结果修改 P00、仓位、TopK、频率或成本口径。
