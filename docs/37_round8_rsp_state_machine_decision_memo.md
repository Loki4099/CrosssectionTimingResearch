# Round 8：RSP-only状态机决策备忘录

Round 8 已按冻结计划完成并在R8C硬停。唯一development-eligible程序是 **P00 raw RSP风险解除即恢复**；raw双阈值P01与A4双标签P02均未提供相对P00的增量。

P00在404个严格outer-OOS周中有49.26%处于DEFENSE，10bp相对同平均暴露static终值为 **+26.68%**，20bp仍为+23.39%，正主动年度比例87.5%，MDD为-18.13%而static为-26.09%，六事件留一最小主动终值+20.40%。它通过全部共同经济门。

P01要求raw `-RSP`也达到历史q75才重新进攻，DEFENSE比例升至79.21%，最长连续防守122周。其10bp active终值仍为+12.88%，但相对P00的周增量为负、block下界为负、Holm p=1；它减少了MAE10暴露，却付出过多错失上涨，因此不晋级。

P02虽然使用A4标签训练的isotonic输出，但在全部404周生成了与P00逐周完全相同的状态。原因是每次raw RSP风险解除时，isotonic预期A4均已大于0；第二标签没有增加一次退出否决或确认。它不是独立进攻信息，也不能改写Round 7的head资格失败。

因此，当前最简洁的完整市场状态程序是P00：raw RSP只负责识别风险，风险分位解除后立即恢复。P01/P02可在未来机械锁箱中作为同时冻结的机制对照，但不应与P00并称development赢家。下一步若获用户授权，Round 9只应将P00迁移到long-only `mom_255_0`；锁箱仍应在迁移完成并冻结全部候选后以sealed two-phase方式另开。

本轮未读取2022–2026 outcome，未运行mom255或任何横截面策略。状态为 `completed_pending_user_round9_freeze_decision`。
