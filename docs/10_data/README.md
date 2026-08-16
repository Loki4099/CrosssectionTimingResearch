# 数据与统一评价口径

- [数据契约与 QA](../02_data_contract_and_qa.md)
- [公司行动会计](../07_corporate_action_accounting.md)
- [最终候选实施与门禁报告](./sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate_implementation_report.md)
- [本地实验运行区规范](./runtime_storage_policy.md)

当前冻结版本为 `sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`，冻结记录位于该 curated 目录的 `FROZEN.json`。它是 `review / free_research_candidate`，17 项门禁和 100 项数据修复验收测试通过，允许研究回测，但 `formal_eligible=false`，运行时必须显式允许 review 数据。统一评价期为 2018-01-02 开盘至 2026-06-30 收盘；任何数据变化必须发布新版本，禁止原地修改。Git 仓库只保存冻结记录、门禁摘要和复现代码，不发布受许可与体积限制的完整行情。
