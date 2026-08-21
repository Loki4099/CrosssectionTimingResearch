# 总设计与研究治理

本层只维护跨主线的治理入口，不重复维护每一轮的当前结果。

## 当前主线

- [系统性 long-only 横截面 Alpha](../research_tracks/cross_sectional_alpha.md)：当前研究主线；XA01单因子与XA02状态图谱已完成，XA03滚动聚合模型计划已冻结并授权、尚未执行。
- [防御择时与仓位控制](../research_tracks/defensive_timing.md)：Round 1–10历史档案；当前结论以主线主页和[跨轮总结](../42_round6_round10_experiment_synthesis.md)为准。

## 共同治理文档

- [研究总计划](../00_research_plan.md)：原始长期目标、共同回测口径和变更控制；
- [数据契约与QA](../02_data_contract_and_qa.md)：市场数据、可得性和质量门；
- [公司行动会计](../07_corporate_action_accounting.md)：拆股、分红、退市与组合会计；
- [实验台账规范](../03_experiment_ledger.md)：experiment/scenario/run层级、状态和审计字段；
- [本地runtime政策](../10_data/runtime_storage_policy.md)：大体积数据和完整bundle的存储边界；
- [实验台账说明](../../experiments/README.md)：预注册快照与执行结果的状态来源优先级。

## 历史冻结计划

Round 1–10计划、amendment、decision memo和batch design均从[防御择时主页](../research_tracks/defensive_timing.md)及[实验档案](../20_experiments/README.md)进入。它们记录各轮当时的授权和硬停止状态，不承担今天的全局状态展示。

跨组规则只能通过新版本计划改变；单个实验不得自行修改数据区间、时点、成本、组合口径或评价指标。历史锁定文件和manifest路径保持append-only。
