# 防御择时与仓位控制

> 状态：**Round 1–10 已完成，作为历史研究档案保留。**
> 最终证据：RSP/SPY63具有持续的风险信息，但冻结P00政策在2022–2026机械揭示中未通过联合门；全部仍为 `formal_eligible=false` 的免费研究证据。

## 1. 这条主线研究什么

防御主线研究“市场总风险暴露应该是多少”。它不替代横截面选股，而是把市场级风险信号映射成100%或50%风险资产仓位，再与裸策略及同平均暴露静态控制比较。

P00不是线性回归或机器学习模型。它使用原始RSP/SPY63参与度信号：63个交易日内RSP相对SPY表现越弱，防御分数越高；分数超过只用历史估计的上四分位阈值时降至50%仓位，风险解除后恢复100%。Round 7的Ridge、GAM和LightGBM候选均未击败这条简单参考。

## 2. 当前结论

| 层级 | 结论 |
|---|---|
| 因子信息 | RSP/SPY63是Round 5唯一robust风险因子；Round 6更换进攻标签后仍保留正向信息，但未过正式资格门 |
| 多因子模型 | Round 7没有合格的risk或attack多变量head |
| 状态政策 | Round 8的P00是唯一development-eligible政策 |
| 横截面迁移 | Round 9在2018–2021开发期六格6/6通过 |
| 机械揭示 | Round 10在2022–2026六格0/6；Sharpe和相对静态timing仍为正，但终值落后裸策略且MDD恶化 |
| 使用边界 | P00可以作为冻结研究对照，不能表述为已经通过部署验证的稳定风控系统 |

完整经济解释见[Round 6–10跨轮总结](../42_round6_round10_experiment_synthesis.md)。

## 3. 实验时间线

| 阶段 | 研究问题 | 轮结束状态 | 主要入口 |
|---|---|---|---|
| G11–G33 | 波动率连续缩放、反转与减仓能否改善裸动量？ | 九宫格完成；long-only整体过度保险，部分WML机制为正但绝对表现弱 | [计划](../21_systematic_experiment_program_v2.md) · [总结](../22_round1_main_grid_synthesis.md) |
| Round 2 | 简单信号和模型能否预测何时SPY应转现金？ | simple gate无候选，复杂阶段停止 | [计划](../23_round2_defense_timing_signal_program_v1.md) · [R2C报告](../20_experiments/R2C_spy_tbill_timing/report.md) |
| Round 3/R3B | 非对称恢复和Ridge持续性确认能否改善退出？ | 两条路线均失败 | [R3A报告](../20_experiments/R3A_asymmetric_reentry/report.md) · [R3B报告](../20_experiments/R3B_recovery_persistence/report.md) |
| Round 4 | 扩展单因子并审计旧标签 | RSP/SPY63仅普通阳性；旧标签存在幅度和期限错位 | [计划](../26_round4_defense_factor_audit_program_v1.md) · [决策](../27_round4_factor_audit_decision_memo.md) |
| Round 5 | 在MAE13/Y5下重新筛选风险因子 | RSP/SPY63成为唯一robust阳性 | [计划](../28_round5_mae13_single_factor_program_v1.md) · [决策](../29_round5_mae13_single_factor_decision_memo.md) |
| Round 6 | 哪些因子识别四周重新进攻机会？ | RSP方向稳定，但三条资格路线均为0 | [计划](../31_round6_attack4_single_factor_program_v1.md) · [决策](../32_round6_attack4_single_factor_decision_memo.md) |
| Round 7 | 多变量模型能否击败简单参考？ | 27个risk process与正式A4 head均0合格 | [计划](../33_round7_dual_head_model_program_draft_v1.md) · [决策](../36_round7_dual_head_model_decision_memo.md) |
| Round 8 | RSP-only信号如何形成风险优先政策？ | P00唯一development-eligible | [计划](../34_round8_risk_veto_state_machine_program_draft_v1.md) · [决策](../37_round8_rsp_state_machine_decision_memo.md) |
| Round 9 | P00能否迁移到long-only `mom_255_0`？ | 开发期六格6/6通过 | [正式计划](../38_round9_p00_mom255_transfer_program_v1.md) · [决策](../39_round9_p00_mom255_transfer_decision_memo.md) |
| Round 10 | 冻结迁移方案能否通过2022–2026机械揭示？ | 失败，六格0/6 | [计划](../40_round10_p00_mom255_mechanical_lockbox_plan_v1.md) · [决策](../41_round10_p00_mom255_mechanical_lockbox_decision_memo.md) |

Round 7和Round 8文件名中的 `draft` 是为历史provenance保留，不代表它们未执行。旧[Round 9草案](../35_round9_mom255_long_only_transfer_program_draft_v1.md)已被Round 9正式计划取代，不是当前规格。

## 4. 代码、配置、结果和图表

| 层级 | 入口 |
|---|---|
| 全部设计与报告 | [实验档案](../20_experiments/README.md) |
| 因子与市场数据 | [round4_factors.py](../../src/momentum_reversal/data/round4_factors.py) |
| Round 2–10管线 | [pipelines目录](../../src/momentum_reversal/pipelines/)中的 `round2_*` 至 `round10_*` |
| 运行、审计和制图脚本 | [scripts目录](../../scripts/)中的 `run_round*`、`audit_round*`、`build_round*_figures.py` |
| 冻结计划与配置 | [config/experiments](../../config/experiments/)中的 `round2` 至 `round10` |
| 计划快照与执行结果 | [experiments说明](../../experiments/README.md) |
| 紧凑发布结果 | [results/published](../../results/published/README.md) |
| 图表 | [图表索引](../figures/README.md) |

历史设计、锁文件、报告和发布manifest均保持原路径，不应为整理目录而移动或重命名。
