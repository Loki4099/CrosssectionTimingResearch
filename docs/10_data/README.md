# 数据与统一评价口径

两条研究主线共享同一套历史时点证券身份、成分、价格、公司行动、交易日历和回测会计。新增基本面数据必须接入这一PIT框架，不能另建使用当前ticker或后来修订值的旁路面板。

## 当前数据状态

| 数据层 | 状态 | 时间范围/边界 | 下一步 |
|---|---|---|---|
| PIT成分、证券身份与市场价格 | **已冻结可用** | 2013-01-02开始暖机；2018-01-02 open至2026-06-30 close统一评价 | 保持版本不变 |
| 公司行动与终止事件 | **已审计可用** | 与冻结市场数据一致 | 新数据版本继续做身份和未来扰动测试 |
| SID↔issuer/CIK/股类有效期 | **缺失** | 当前security master没有可用于基本面的历史CIK层 | 基本面接入第一道门 |
| SEC基本面PIT | **数据源已选、尚未实施** | 计划从2009年EDGAR/XBRL开始，按各因子历史需求形成不同合法起点 | 建立filing vintage与as-of特征面板 |
| 新闻与媒体文本 | **本主线不实施** | 由独立任务研究 | 不进入首轮市场+基本面实验 |

当前冻结版本为 `sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`。它是 `review / free_research_candidate`，17项门禁和100项数据修复验收测试通过，允许研究回测，但 `formal_eligible=false`，运行时必须显式允许review数据。

## 市场数据入口

- [数据契约与QA](../02_data_contract_and_qa.md)
- [公司行动会计](../07_corporate_action_accounting.md)
- [最终候选实施与门禁报告](./sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate_implementation_report.md)
- [冻结数据元信息](../../metadata/frozen_dataset/)
- [本地实验运行区规范](./runtime_storage_policy.md)

## 基本面PIT原则

免费主源采用[SEC EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)及其[2009年以来的as-filed财务报表数据集](https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets)。数据下载和因子计算尚未开始；后续实现至少必须满足：

1. 用带有效期的issuer/CIK/股类映射连接项目SID，禁止用今天的ticker回填历史；
2. 保留accession、form、period、accepted timestamp、amendment、taxonomy、unit和dimension；
3. 每个信号时点只能选择当时已经被SEC接受的filing vintage；后续修订从新接受时点起生效，不能覆盖过去；
4. 区分10-Q累计值、单季值、10-K比较期间和TTM构造；
5. 按因子所需历史决定首个合法日期，不为统一样本而使用未来数据补齐；
6. 对未来filing做扰动时，历史特征必须逐值不变；缺失和不适用不能静默填0。

初期优先支持主表能够稳定构造的价值、盈利质量、应计、投资和发行类信号。复杂注释、分析师预期、期权和媒体数据不属于这一基本面MVP。

## 存储边界

任何数据变化必须发布新版本，禁止原地修改。Git只保存采集代码、来源台账、配置、哈希、质量摘要和紧凑结果；完整行情、SEC原始申报、curated大表和大体积运行bundle保留在本地runtime。
