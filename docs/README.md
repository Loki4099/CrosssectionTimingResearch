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

免费研究数据集 `sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate` 已通过门禁并冻结。[G00](./20_experiments/G00_baseline/report.md)、[G11](./20_experiments/G11_spy_continuous_scale/report.md)、[G12](./20_experiments/G12_book_hist_continuous_scale/report.md)、[G13](./20_experiments/G13_book_forecast_continuous_scale/report.md)、[G21](./20_experiments/G21_spy_reversal/report.md)、[G22](./20_experiments/G22_book_hist_reversal/report.md)、[G23](./20_experiments/G23_book_forecast_reversal/report.md)、[G31](./20_experiments/G31_spy_derisk/report.md)、[G32](./20_experiments/G32_book_hist_derisk/report.md)与 [G33](./20_experiments/G33_book_forecast_derisk/report.md)已完成，九宫格主网格闭合。G23 的 LO 0/36 失败；LS 以 33/36、月 15/18、周 18/18 通过平台门槛，但仍成本/借券敏感且绝对表现弱。所有结果仍为 `formal_run_eligible=false` 的免费研究证据；XS01 为另行预注册的补充实验。
