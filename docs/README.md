# 研究文档中心

新访问者应先按研究问题选择主线；历史文件继续按原编号和路径保存，不需要从34份根级文档中自行拼接当前状态。

## 两条研究主线

| 主线 | 定位 | 当前状态 | 一站式入口 |
|---|---|---|---|
| 系统性 long-only 横截面 Alpha | 研究在历史时点S&P 500中持有哪些股票 | **当前主线**；文献/候选登记完成，SEC基本面层和首轮单因子计划待建 | [横截面Alpha主页](./research_tracks/cross_sectional_alpha.md) |
| 防御择时与仓位控制 | 研究何时降低市场总风险暴露 | **历史研究档案**；Round 1–10完成，P00机械揭示未通过 | [防御择时主页](./research_tracks/defensive_timing.md) |

## 共享文档层

| 内容 | 入口 | 用途 |
|---|---|---|
| 总设计与治理 | [00_program](./00_program/README.md) | 研究边界、变更控制、证据语言 |
| 数据与PIT | [10_data](./10_data/README.md) | 市场数据、基本面数据、实体映射、版本和QA |
| 实验设计与报告 | [20_experiments](./20_experiments/README.md) | 按主线和轮次进入design/report/code/result/figure |
| 论文与候选定义 | [30_references](./30_references/README.md) | 论文、复现定义和机器登记表 |
| 图表 | [figures](./figures/README.md) | 各轮已发布图表和生成脚本 |
| 紧凑机器结果 | [results/published](../results/published/README.md) | CSV、JSON、manifest和小型审计工件 |

## 当前工作顺序

横截面Alpha当前按以下顺序推进：

1. 整理文档与主线边界；
2. 冻结市场+SEC基本面的实体、时间可得性和因子执行范围；
3. 制定首轮论文定义单因子实验；
4. 根据单因子、相关性和集中度结果设计信号聚合；
5. 最后再比较模型、组合构建和冻结P00协同。

目前只有第1步完成。知识登记不等于实验授权，也没有已经存在的“Round 11”结果。

## 历史文档如何阅读

- [研究总计划](./00_research_plan.md)记录原始动量—反转研究章程及其后续状态注记，是历史背景，不是横截面新计划。
- [Round 1主网格总结](./22_round1_main_grid_synthesis.md)和[Round 6–10跨轮总结](./42_round6_round10_experiment_synthesis.md)分别概括两段防御研究。
- `33`、`34`文件名中的 `draft` 为provenance兼容保留，但对应轮次已经执行；`35`是被`38`正式计划取代的历史草案。
- 运行前registry可能继续显示“未运行”。当前机器状态以执行结果、published decision/manifest和报告为准，见[实验台账说明](../experiments/README.md)。

冻结设计、锁、报告和历史结果不因本次信息架构整理而移动、重命名或追溯改写。
