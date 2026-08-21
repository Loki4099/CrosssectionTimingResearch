# 系统性 long-only 横截面 Alpha

> 状态：**当前研究主线；市场与SEC数据已认证，XA01首轮单因子实验与XA02因子路径/市场状态图谱均已完成；等待用户审阅后另行设计XA03。**
> 股票池：历史时点 S&P 500 成分股。
> 当前执行范围：市场数据与 SEC 基本面；新闻与文本研究由独立任务处理。

## 1. 研究目标

本主线研究“同一时点应该持有哪些股票”，而不是“市场总仓位应该是多少”。第一阶段保持组合构建尽量简单，用 Top5、Top10、Top20、Top50 long-only 组合检验单个横截面信号；只有完成单因子证据和相关性审计后，才进入信号聚合、模型比较与组合优化。

计划中的研究顺序是：

1. 建立可审计的市场数据与 SEC 基本面 PIT 数据层；
2. 按论文主定义逐个测试原子因子；
3. 检查横截面相关、名单重合、行业与个股贡献集中；
4. 根据机制与经验冗余形成聚合候选；
5. 比较透明聚合、线性模型和受限非线性模型；
6. 冻结合格裸策略后，再以不调参的 P00 做协同诊断。

这是一条路线说明，不是实验授权。具体标签、调仓频率、成本、walk-forward 训练、通过门槛和路径数量将在首轮计划中另行确定。

## 2. 当前进度

| 层级 | 当前状态 | 事实来源 |
|---|---|---|
| 市场与成分数据 | 冻结免费研究数据可用；2013年暖机，2018-01-02至2026-06-30统一评价 | [数据入口](../10_data/README.md) |
| 文献与候选定义 | 55篇论文/方法记录、55个原始候选概念；append-only定义表共57行，含1个修正版与1个项目翻译 | [登记说明](../43_cross_sectional_alpha_literature_and_factor_registry_v1.md) |
| 基本面源数据 | SEC EDGAR不可变响应、有效期CIK桥、filing vintage与年度PIT规范事实已冻结认证；`formal_eligible=false` | [数据合同](../10_data/cross_sectional_market_fundamental_database_v1.md) · [认证证据](../../results/published/cross_sectional_data/xs-market-sec-source-data-20260820-v1/) |
| 因子库与因子面板 | 17个登记因子、162个月末、1,380,944行已冻结审计；14个因子通过纯数据门，BM与净派现收益率阻断 | [登记规则](../../config/research/cross_sectional_alpha/README.md) · [认证证据](../../results/published/cross_sectional_data/xs-market-sec-bundle-20260820-v1/) |
| 裸策略基准 | G00 已完成；`mom_255_0 / Top20 / monthly` 是后续比较锚，不代表未来冠军 | [G00报告](../20_experiments/G00_baseline/report.md) |
| XS01 | 仅有历史配置与catalog登记；没有runner、报告、发布结果或图表 | [XS01配置](../../config/experiments/XS01.toml) |
| XA01原子因子实验 | 已完成；严格EQ为0，XS003在周/月频作为趋势维度代表；G00 Top50存在已记录身份例外 | [计划](../44_xa01_atomic_factor_walkforward_program_v1.md) · [报告](../20_experiments/XA01_atomic_factor_walkforward/report.md) · [结果](../../results/published/cross_sectional_alpha/XA01/) |
| XA02因子路径与市场状态图谱 | 已完成并硬停；336个1D与168个2D检验完整，2个1D条件关系通过全部门，稳健2D关系为0；模型关闭 | [计划](../45_xa02_factor_market_state_atlas_program_v1.md) · [设计](../20_experiments/XA02_factor_market_state_atlas/design.md) · [报告](../20_experiments/XA02_factor_market_state_atlas/report.md) · [结果](../../results/published/cross_sectional_alpha/XA02/) |
| 聚合与模型 | 等待XA02结果；之后另行设计factor-only与factor+state滚动模型 | 本页 |

## 3. 论文与因子知识层

- 人类可读说明：[论文与因子定义登记 v1](../43_cross_sectional_alpha_literature_and_factor_registry_v1.md)
- 论文机器表：[paper_registry.csv](../../config/research/cross_sectional_alpha/paper_registry.csv)
- 因子机器表：[factor_definition_registry.csv](../../config/research/cross_sectional_alpha/factor_definition_registry.csv)
- 登记规则：[config/research/cross_sectional_alpha/README.md](../../config/research/cross_sectional_alpha/README.md)

登记表是宽候选知识库，不等于下一轮执行清单。因子数据库的数据资格只说明公式、PIT输入和覆盖率可用；新闻、文本、期权、分析师和供应链候选可以继续留在知识库，14个数据合格因子也没有自动获得回测实验资格。

## 4. 已有横截面基准

G00 是目前唯一从配置、runner、回测、报告到发布结果全部闭合的裸横截面实验：

- 设计与报告：[G00 design](../20_experiments/G00_baseline/design.md) · [G00 report](../20_experiments/G00_baseline/report.md)
- 配置：[G00.toml](../../config/experiments/G00.toml)
- 因子实现：[momentum.py](../../src/momentum_reversal/factors/momentum.py)
- 排名与Top-K：[ranking.py](../../src/momentum_reversal/portfolio/ranking.py)
- 回测引擎：[engine.py](../../src/momentum_reversal/backtest/engine.py)
- 执行管线：[g00.py](../../src/momentum_reversal/pipelines/g00.py)
- 机器结果：[results/published/G00](../../results/published/G00/)
- 图表：G00作为共同裸控制出现在[Round 1图表](../figures/round1/README.md)中

Round 9–10把P00叠加到 `mom_255_0`，属于两条主线的接口实验，不是新的横截面因子证据。其结论从本主线的裸策略筛选中隔离，详见[防御择时主页](./defensive_timing.md)。

## 5. 后续文档应落在哪里

首轮市场与基本面单因子计划形成后，应分别建立：

- `config/experiments/` 下的新横截面实验命名空间；
- `docs/20_experiments/` 下的设计与报告；
- `src/momentum_reversal/` 下可复用的基本面PIT、因子和runner代码；
- `experiments/` 下不可覆盖的计划快照与执行结果；
- `results/published/` 下紧凑机器证据；
- `docs/figures/` 下对应图表。

XA02已经完成并硬停。用户审阅后再冻结XA03的factor-only与factor+state滚动模型；其Target、训练记忆、refit节奏、模型容量和输入集合都不由XA02自动决定。后续仍按“相关性与机制去冗余→透明聚合/低容量模型→组合构建→冻结P00迁移”的顺序推进；任何聚合、模型或P00实验都需要新计划授权。
