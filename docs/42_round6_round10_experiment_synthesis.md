# Round 6–10 防守—进攻—横截面迁移实验总结

本文件整理从A4进攻因子审计到P00机械锁箱的完整证据链。各轮机器结论保持独立；后续用户授权不追溯改写前一轮的自动晋级结果。

## 总览

| Round | 研究问题 | 冻结结论 |
|---|---|---|
| 6 | 哪些单因子能识别未来4周重新进攻机会？ | RSP/SPY63方向信息为正，但20臂BH与经济/条件门均未产生合格候选；三条资格路线为0。 |
| 7 | 多因子与LightGBM等模型能否形成合格risk/attack head？ | 27个risk processes均未合格；正式A4 head也未合格。复杂模型没有击败冻结简单参考。 |
| 8 | 是否仍可用原始RSP构造风险优先状态机？ | P00为唯一development-eligible程序：RSP风险高时50%暴露，风险解除后立即恢复100%；P01无增量，P02与P00状态相同。 |
| 9 | P00能否改善long-only `mom_255_0`？ | 2018–2021开发期Top20-monthly primary及六个TopK×频率格全部通过终值、timing、Sharpe和MDD联合门。 |
| 10 | 冻结的Round 9候选能否通过2022–2026机械防火墙？ | 未通过。Primary与六格family均失败，`mechanical_lockbox_passed=false`。 |

## 从因子到政策

Round 6说明RSP/SPY63在A4标签下仍有正向排序信息，但没有通过预注册的多重比较和经济资格门。因此它不是Round 6自动选出的attack champion。后续实验保留它，是用户依据Round 5风险证据、Round 6跨标签方向一致性和机制可解释性作出的显式研究选择。

Round 7进一步表明，在现有样本、特征语法和冻结模型预算下，多因子/复杂模型没有形成合格双head。该结果把研究重新推回最简单的raw RSP基准，而不是证明所有机器学习组合永久无效。

Round 8只比较三条有限RSP政策。P00使用单一风险头控制1.0/0.5暴露；attack信号不得单独推翻高风险判断。P00在开发期胜出，但仍只是策略映射，不等同于RSP因子本身。

## 开发期成功与锁箱失败

Round 9在2018–2021开发期得到很强的迁移结果：Top20-monthly 10bp下，P00 overlay终值比裸策略高18.30%，matched-static timing为+24.16%，Sharpe从0.442提高到0.750，MDD从-37.75%改善到-26.60%；六格6/6通过。

Round 10保持P00、TopK、频率、仓位和成本不变，先冻结2022–2026状态与股票目标，再揭示结果。Top20-monthly 10bp下：

| 指标 | P00 overlay | naked | 结论 |
|---|---:|---:|---|
| 终值NAV | 3.4922 | 4.0927 | 相对少14.67% |
| CAGR | 32.30% | 37.08% | 少4.78个百分点 |
| Sharpe | 1.288 | 1.202 | 提高0.085 |
| MDD | -27.21% | -23.80% | 恶化3.41个百分点 |
| matched-static timing | — | — | +13.50% |

六格在10bp下均有正timing和正Sharpe增量，但全部落后对应裸策略终值，只有两个月频格改善MDD，联合门为0/6。Primary的13周moving-block下界为负、p=0.1176；删除2024后timing转为-3.79%，逐年留一门失败。

## 如何解释RSP/SPY63

锁箱没有证明RSP/SPY63完全失效。动态P00仍优于同平均风险暴露的静态控制，且显著降低日收益波动，因此Sharpe提高。失败发生在固定政策层：集中型大牛市中，RSP落后SPY可以持续很久而不立即转化成指数下跌；固定减仓50%造成机会成本，并可能在反弹时防守、下一段下跌前恢复满仓。

因此当前证据应分三层表述：

1. **因子层**：RSP/SPY63包含可重复的市场参与度/集中度信息，值得保留。
2. **政策层**：冻结P00的1.0/0.5映射不具备跨阶段稳定的完整策略改进能力。
3. **部署层**：Round 9开发成功没有通过Round 10机械锁箱，当前候选不得提升为经确认的部署策略。

## 治理状态

Round 10已完成并硬停，不自动修改q75、仓位、退出规则、TopK、频率或成本，也不允许其他格递补。全部结果仍为免费研究数据上的 `formal_eligible=false` 证据。若继续研究保险强度或状态映射，2022–2026已经是已观察开发信息，新的确认只能依赖另购独立数据或未来paper/live forward记录。

详细证据见[Round 6决策](./32_round6_attack4_single_factor_decision_memo.md)、[Round 7决策](./36_round7_dual_head_model_decision_memo.md)、[Round 8决策](./37_round8_rsp_state_machine_decision_memo.md)、[Round 9决策](./39_round9_p00_mom255_transfer_decision_memo.md)和[Round 10决策](./41_round10_p00_mom255_mechanical_lockbox_decision_memo.md)。
