# 数据与统一评价口径

两条研究主线共享同一套历史时点证券身份、成分、价格、公司行动、交易日历和回测会计。新增基本面数据必须接入这一PIT框架，不能另建使用当前ticker或后来修订值的旁路面板。

## 当前数据状态

| 数据层 | 状态 | 时间范围/边界 | 下一步 |
|---|---|---|---|
| PIT成分、证券身份与市场价格 | **已冻结可用** | 2013-01-02开始暖机；2018-01-02 open至2026-06-30 close统一评价 | 保持版本不变 |
| 公司行动与终止事件 | **已审计可用** | 与冻结市场数据一致 | 新数据版本继续做身份和未来扰动测试 |
| SID↔issuer/CIK有效期 | **已认证可用** | 745/745 SID；772个有效期CIK区间；1,701,149/1,701,149成员交易日已映射 | 后续新证券版本继续做时序支持审计 |
| SEC基本面PIT源层 | **已冻结并认证** | 753/753研究期CIK完成；2,386,818条filing、1,679,666条注册事实、358,303条规范年度事实 | 因子层按定义生成合法起点与覆盖率 |
| 市场与基本面因子面板 | **延期** | 候选登记和实现脚手架不构成已计算或合格状态 | 后续轮次单独构建、QA和授权 |
| 新闻与媒体文本 | **本主线不实施** | 由独立任务研究 | 不进入首轮市场+基本面实验 |

当前冻结版本为 `sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`。它是 `review / free_research_candidate`，17项门禁和100项数据修复验收测试通过，允许研究回测，但 `formal_eligible=false`，运行时必须显式允许review数据。

## 市场数据入口

- [数据契约与QA](../02_data_contract_and_qa.md)
- [公司行动会计](../07_corporate_action_accounting.md)
- [最终候选实施与门禁报告](./sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate_implementation_report.md)
- [冻结数据元信息](../../metadata/frozen_dataset/)
- [横截面市场与SEC基本面源数据库 v1](./cross_sectional_market_fundamental_database_v1.md)
- [紧凑源数据认证证据](../../results/published/cross_sectional_data/xs-market-sec-source-data-20260820-v1/)
- [本地实验运行区规范](./runtime_storage_policy.md)

## 基本面PIT源层

免费主源采用[SEC EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)。不可变原始响应、SID–CIK桥、申报事件、注册事实和年度PIT规范事实已经完成，并由[source-only认证包](../../results/published/cross_sectional_data/xs-market-sec-source-data-20260820-v1/)锚定。该认证不读取因子面板，也不评价任何交易结果。实现遵循：

1. 用带有效期的issuer/CIK/股类映射连接项目SID，禁止用今天的ticker回填历史；
2. 保留accession、form、period、accepted timestamp、amendment、taxonomy、unit和dimension；
3. 每个信号时点只能选择当时已经被SEC接受的filing vintage；后续修订从新接受时点起生效，不能覆盖过去；
4. 区分10-Q累计值、单季值、10-K比较期间和TTM构造；
5. 后续按因子所需历史决定首个合法日期，不为统一样本而使用未来数据补齐；
6. 会计恒等式和历史实体支持从权威表独立重算；缺失和不适用不能静默填0。

源层认证结果为：753/753 CIK完成、聚合失败0；751个CIK为可用，FRC与SBNY两项经严格SEC证据审阅标为`resolved_not_applicable`且没有插补；29,146个会计恒等式上下文直接重算失败0；766个历史实体区间时序失败0。数据仍为研究级 `formal_eligible=false`。

因子公式、因子覆盖率、统一因子面板和实验资格均延期。复杂注释、分析师预期、期权和媒体数据不属于这一源数据库版本。

## 存储边界

任何数据变化必须发布新版本，禁止原地修改。Git只保存采集代码、来源台账、配置、哈希、质量摘要和紧凑结果；完整行情、SEC原始申报、curated大表和大体积运行bundle保留在本地runtime。
