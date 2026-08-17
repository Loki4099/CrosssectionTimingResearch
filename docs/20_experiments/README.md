# 实验组导航

| 组 | 动作 | 风险变量 | 当前阶段 |
|---|---|---|---|
| [G00](./G00_baseline/design.md)（[报告](./G00_baseline/report.md)） | 裸策略控制组 | 无 | 已在冻结 v3 数据完成并通过 free-research 验收 |
| [G11](./G11_spy_continuous_scale/design.md)（[报告](./G11_spy_continuous_scale/report.md)） | 连续约束 | SPY 历史波动率 | 已完成：long-only H1 0/18 失败、CAGR/Sharpe 18/18 下降且 MDD 18/18 改善；long-short 机制在全部成本/借券压力场景中稳健，但绝对表现弱 |
| [G12](./G12_book_hist_continuous_scale/design.md)（[报告](./G12_book_hist_continuous_scale/report.md)） | 连续约束 | book 历史波动率 | 已完成：LO H1 0/18 失败、CAGR/Sharpe 18/18 下降、MDD 16/18 改善且持续过度保险；LS 17/18 主场景及 204/216 压力场景同时改善 Sharpe/MDD，但绝对表现弱 |
| [G13](./G13_book_forecast_continuous_scale/design.md)（[报告](./G13_book_forecast_continuous_scale/report.md)） | 连续约束 | book 未来预测波动率 | 已完成：LO H1 0/18 失败，CAGR/Sharpe 18/18 下降、MDD 18/18 改善且持续过度保险；LS 12/18 主场景与 132/216 压力场景同时改善 Sharpe/MDD，但成本/借券敏感且绝对表现弱 |
| [G21](./G21_spy_reversal/design.md)（[报告](./G21_spy_reversal/report.md)） | 高波反转 | SPY 历史波动率 | 已完成：long-only 失败负对照，WML 机制成立但绝对收益弱 |
| [G22](./G22_book_hist_reversal/design.md)（[报告](./G22_book_hist_reversal/report.md)） | 高波反转 | book 历史波动率 | 已完成：LO 4/36 联合改善、H1 失败；LS 23/36 且月频 8/18，未过平台门槛，周频为成本敏感局部机制 |
| [G23](./G23_book_forecast_reversal/design.md)（[报告](./G23_book_forecast_reversal/report.md)） | 高波反转 | book 未来预测波动率 | 已完成：LO 0/36 失败；LS 33/36、月15/18、周18/18 通过平台门槛，但成本/借券敏感且绝对表现弱 |
| [G31](./G31_spy_derisk/design.md)（[报告](./G31_spy_derisk/report.md)） | 高波减仓 | SPY 历史波动率 | 已完成：long-only 回撤 18/18 改善但 H1 失败；long-short 机制为正、绝对表现弱 |
| [G32](./G32_book_hist_derisk/design.md)（[报告](./G32_book_hist_derisk/report.md)） | 高波减仓 | book 历史波动率 | 已完成：long-only H1 失败、CAGR/Sharpe 18/18 下降且 MDD 混合；long-short 17/18 同时改善 Sharpe/MDD，压力下机制稳健但绝对表现弱 |
| [G33](./G33_book_forecast_derisk/design.md)（[报告](./G33_book_forecast_derisk/report.md)） | 高波减仓 | book 未来预测波动率 | 已完成：long-only H1 0/18 失败、CAGR/Sharpe 18/18 下降且 MDD 18/18 改善；long-short 10/18 同时改善 Sharpe/MDD，MDD 改善稳健，Sharpe 对成本/借券费敏感且绝对表现弱 |
| XS01 | 横截面风险调整 | 个股历史波动率 | 未在冻结数据上运行；九宫格外 |

每组在进入执行波次时建立独立的 `design.md` 和 `report.md`。九宫格主网格现已全部完成，仍为 `formal_run_eligible=false` 的免费研究证据；G22 v1 因 provenance 文字不一致保留为未发布无效证据，有效运行是完整重跑的 v2。G23 的 LS 是首个跨总数和双频率门槛的平台结果，但不构成 long-only 或部署支持。XS01 为单独预注册的九宫格外补充实验。

## Round 2（已按门禁停止）

[第二轮防御时点信号筛选与模型比较计划 v1](../23_round2_defense_timing_signal_program_v1.md)是已冻结并执行过的历史计划。免费核心数据、R2B 与 R2C simple development 均已完成；没有 process 同时通过信号与经济门，因此复杂阶段、锁箱和 `mom_255_0` 迁移按机器门禁停止，不能在本轮提升 runner-up。

- [R2A_DATA 数据扩展冻结设计](./R2A_DATA/design.md)（[采集日志](./R2A_DATA/acquisition_log.md)）：免费 L 线 `r2a-long-free-20260816-v1` 已完成不可变 candidate 与双构建哈希验收；SPY/RF required gate 通过，VIX optional F3 因官方源缺一个周信号而取消。Norgate/PIT 增量线按用户决定暂缓；尚未计算任何 target、模型或策略结果。
- [R2B_SIGNAL_DIAGNOSTICS 冻结设计](./R2B_signal_diagnostics/design.md)（[报告](./R2B_signal_diagnostics/report.md)）：`r2b-free-core-v1` 已通过公式、哈希、不可变和锁箱防火墙验收。单因子对下一周现金胜 SPY 的 AUC 仅 `0.471–0.517`，但 RV/回撤等可识别随后四周路径风险；没有产生 champion，只开放 R2C simple development。
- [R2C_SPY_TBILL 冻结设计](./R2C_spy_tbill_timing/design.md)（[报告](./R2C_spy_tbill_timing/report.md)）：simple development 已完成，没有 process 同时通过信号与经济硬门；复杂模型未开放，mechanical lockbox 未查看，R2D 因无 candidate 停止。

## Round 3（已在开发期完成并失败）

[非对称防守与重新进攻计划 v1](../24_round3_asymmetric_defense_reentry_program_v1.md)是 Round 2 停止后的独立开发计划。[R3A](./R3A_asymmetric_reentry/design.md)（[报告](./R3A_asymmetric_reentry/report.md)）已完成：价格恢复出口虽减少 68.7% 的错失上涨，却损失更多防守收益，相对对称 RV21 终值低 14.16%，只保留 5.43% 的 MDD 改善；H1–H4 全失败，锁箱继续封存。

[R3B 恢复持续性确认计划](../25_round3b_recovery_persistence_program_v1.md)（[设计](./R3B_recovery_persistence/design.md)，[报告](./R3B_recovery_persistence/report.md)）已完成：固定 Ridge 的 Brier skill 为 -1.29%、AUC 为 0.480，10bp 终值相对对称 RV21 低 21.67%；H1–H4 全失败，锁箱与动量迁移继续封存。

## Round 4（已完成并按计划停止）

[原目标单因子扩展、target 审计与熊市事件图谱计划 v1](../26_round4_defense_factor_audit_program_v1.md)已经完成，并形成[决策备忘录](../27_round4_factor_audit_decision_memo.md)。[R4A](./R4A_factor_data/report.md)确认20臂中17臂可用；[R4B](./R4B_t2_single_factor/report.md)只有RSP/SPY63获得普通参考阳性、0臂通过robust门；[R4C](./R4C_target_sanity/report.md)确认一周二分类的主要问题是幅度丢失与期限错位；[R4D](./R4D_spy_drawdown_atlas/report.md)得到10/5/3个-10/-15/-20%事件并显示风险因子有局部预警但误报很多。

程序状态为 `completed_pending_user_target_decision`。模型、多 target 投票、仓位搜索、`mom_255_0` 迁移和 2022–2026 锁箱均未打开。

## Round 5（已完成并按计划停止）

[连续MAE13单因子复审计划](../28_round5_mae13_single_factor_program_v1.md)已完成，并形成[决策备忘录](../29_round5_mae13_single_factor_decision_memo.md)。[R5A](./R5A_mae13_target/report.md)冻结了 `max(MAE13-5%,0)`；[R5B](./R5B_mae13_single_factor/report.md)发现RSP/SPY63是唯一FDR后阳性；[R5C](./R5C_spy_cash_proxy/report.md)确认其相对同暴露静态控制有正timing value；[R5D](./R5D_mae13_robustness/report.md)确认逐一剔除主要回撤后仍不翻转。最终1条robust、1条普通reference，程序停止等待用户决定是否做低维组合或直接冻结单因子候选。锁箱、模型与动量迁移仍关闭。
