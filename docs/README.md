# 研究文档导航

本目录按“总设计—数据—实验—参考”组织；分类入口如下：

- [总设计与治理](./00_program/README.md)
- [数据与统一评价口径](./10_data/README.md)
- [实验组与报告](./20_experiments/README.md)
- [论文与参考资料](./30_references/README.md)

第一轮冻结总设计：[动量—反转系统化实验计划 v2](./21_systematic_experiment_program_v2.md)。

第一轮完整复盘：[主网格实验总结](./22_round1_main_grid_synthesis.md)。

第二轮入口：[防御时点信号筛选与模型比较计划 v1](./23_round2_defense_timing_signal_program_v1.md)。免费长样本核心线、[R2B 信号诊断](./20_experiments/R2B_signal_diagnostics/report.md)与 [R2C 简单阶段](./20_experiments/R2C_spy_tbill_timing/report.md)已经完成；没有 simple benchmark，因而复杂模型、mechanical lockbox 与动量迁移均按预注册门禁停止。

第三轮开发入口：[非对称防守与重新进攻计划 v1](./24_round3_asymmetric_defense_reentry_program_v1.md)。[R3A 报告](./20_experiments/R3A_asymmetric_reentry/report.md)已完成：简单 SMA21 价格恢复出口未能保留防守价值，H1–H4 全失败；2022–2026 锁箱和动量迁移均未开放。

后续独立开发：[R3B 恢复持续性确认计划 v1](./25_round3b_recovery_persistence_program_v1.md)（[报告](./20_experiments/R3B_recovery_persistence/report.md)）也已完成。固定 Ridge 的 Brier skill、AUC、收益排序与经济门均失败；锁箱和 `mom_255_0` 迁移继续关闭。

第四轮入口：[原目标单因子扩展、target 审计与熊市事件图谱计划 v1](./26_round4_defense_factor_audit_program_v1.md)及[决策备忘录](./27_round4_factor_audit_decision_memo.md)。R4A–R4D 已按两道机器锁完成：17/20因子通过数据门，只有RSP/SPY63获普通参考阳性，0条通过robust门；target审计支持下一步研究连续MAE13而非直接堆复杂模型。程序已停止等待用户选择，动量迁移与2022–2026锁箱仍关闭。

第五轮入口：[连续MAE13单因子复审计划 v1](./28_round5_mae13_single_factor_program_v1.md)及[决策备忘录](./29_round5_mae13_single_factor_decision_memo.md)。RSP/SPY63 participation proxy 是17条冻结因子中唯一robust阳性；10bp统一代理几乎保留同期always-SPY CAGR并把MDD从-33.70%降至-22.94%。该结果仍仅为2009–2021开发证据，2022–2026锁箱、模型和动量迁移均未开放。

路线起点：[防守—进攻双头条件路线 v1](./30_defense_attack_dual_head_route_v1.md)、[第六轮 A4 单因子审计计划 v1](./31_round6_attack4_single_factor_program_v1.md)与[Round 6 决策备忘录](./32_round6_attack4_single_factor_decision_memo.md)。Round 6 已完成：RSP/SPY63 level有正向开发期信息，但20项BH与经济门未过；最终direct/economic/conditional三路线均为0。Round 7并非自动晋级，而是经用户重新授权并另行预注册后启动。

Round 7 已按[冻结双head模型计划](./33_round7_dual_head_model_program_draft_v1.md)完成，并形成[决策备忘录](./36_round7_dual_head_model_decision_memo.md)：27个risk processes与正式A4 head均为0条合格。随后[Round 8 RSP-only状态机](./34_round8_risk_veto_state_machine_program_draft_v1.md)完成，并形成[决策备忘录](./37_round8_rsp_state_machine_decision_memo.md)：P00 raw RSP风险解除即恢复是唯一development-eligible程序。[Round 9 P00 × mom_255_0 long-only迁移](./38_round9_p00_mom255_transfer_program_v1.md)在开发期六格全部通过；但后续[Round 10机械锁箱](./40_round10_p00_mom255_mechanical_lockbox_plan_v1.md)（[决策备忘录](./41_round10_p00_mom255_mechanical_lockbox_decision_memo.md)）未通过：2022–2026六格0/6通过联合门，primary虽有正timing value和Sharpe增量，却落后裸策略终值且MDD恶化。

当前完整结论见[Round 6–10跨轮实验总结](./42_round6_round10_experiment_synthesis.md)。

免费研究数据集 `sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate` 已通过门禁并冻结。[G00](./20_experiments/G00_baseline/report.md)、[G11](./20_experiments/G11_spy_continuous_scale/report.md)、[G12](./20_experiments/G12_book_hist_continuous_scale/report.md)、[G13](./20_experiments/G13_book_forecast_continuous_scale/report.md)、[G21](./20_experiments/G21_spy_reversal/report.md)、[G22](./20_experiments/G22_book_hist_reversal/report.md)、[G23](./20_experiments/G23_book_forecast_reversal/report.md)、[G31](./20_experiments/G31_spy_derisk/report.md)、[G32](./20_experiments/G32_book_hist_derisk/report.md)与 [G33](./20_experiments/G33_book_forecast_derisk/report.md)已完成，九宫格主网格闭合。G23 的 LO 0/36 失败；LS 以 33/36、月 15/18、周 18/18 通过平台门槛，但仍成本/借券敏感且绝对表现弱。所有结果仍为 `formal_run_eligible=false` 的免费研究证据；XS01 为另行预注册的补充实验。
