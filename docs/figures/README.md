# 图表索引

本目录保存可提交Git的研究图表。完整日频NAV、持仓和交易账簿仍保留在本地runtime；图表必须能回溯到对应报告和机器结果。

| 主线/阶段 | 图表入口 | 代表图 | 生成脚本 | 说明 |
|---|---|---|---|---|
| 横截面G00与防御九宫格 | [Round 1图集](./round1/README.md) | [long-only净值总览](./round1/overview-nav-long-only.png) | [build_round1_figures.py](../../scripts/build_round1_figures.py) | G00作为共同裸控制；G11–G33为overlay实验 |
| Round 4 | [目录](./round4/) | [单因子主动财富](./round4/r4b-active-wealth.png) · [target审计](./round4/r4c-target-sanity.png) | [build_round4_figures.py](../../scripts/build_round4_figures.py) | 另含17张事件图谱 |
| Round 5 | [目录](./round5/) | [MAE13单因子排名](./round5/r5b-single-factor-ranking.png) | [build_round5_figures.py](../../scripts/build_round5_figures.py) | RSP/SPY63成为唯一robust阳性 |
| Round 6 | [目录](./round6/) | [A4单因子RankIC](./round6/r6-single-factor-rankic.png) | [build_round6_figures.py](../../scripts/build_round6_figures.py) | 进攻标签单因子审计 |
| Round 7 | [目录](./round7/) | [risk outer-OOS RankIC](./round7/r7-risk-outer-oos-rankic.png) | [publish_round7.py](../../scripts/publish_round7.py) | 多模型锦标赛 |
| Round 8 | [目录](./round8/) | [政策主动财富](./round8/r8-policy-active-wealth.png) | [publish_round8.py](../../scripts/publish_round8.py) | RSP-only状态政策 |
| Round 9 | [目录](./round9/) | [开发期primary NAV](./round9/r9-primary-nav.png) | [publish_round9.py](../../scripts/publish_round9.py) | P00 × `mom_255_0`迁移 |
| Round 10 | [目录](./round10/) | [机械揭示primary NAV](./round10/r10-primary-nav.png) | [publish_round10.py](../../scripts/publish_round10.py) | 2022–2026联合门0/6 |

Round 2和Round 3目前没有提交到Git的正式图表；其结论以[实验报告](../20_experiments/README.md)和紧凑机器证据为准。
