# Systematic S&P 500 Alpha Research

一个可审计、可复现的美国股票研究框架，围绕两类不同问题展开：**横截面选股**决定持有哪些股票，**防御择时**决定市场总仓位是多少。历史代码包名 `momentum_reversal` 为兼容保留，不再代表项目只研究动量与反转。

## 从这里开始

| 研究主线 | 当前状态 | 入口 |
|---|---|---|
| 系统性 long-only 横截面 Alpha | **当前主线**。XA01–XA03已完成；XA04以统一CORE10完整样本补做公平的Ridge/LightGBM比较 | [主线主页](docs/research_tracks/cross_sectional_alpha.md) |
| 防御择时与仓位控制 | **已完成研究档案**。Round 1–10闭合；RSP/SPY63有风险信息，但P00在2022–2026机械揭示中未通过联合门 | [主线主页](docs/research_tracks/defensive_timing.md) |

项目总导航见[研究文档中心](docs/README.md)。实验设计与报告、机器结果和图表分别从[实验档案](docs/20_experiments/README.md)、[发布结果](results/published/README.md)和[图表索引](docs/figures/README.md)进入。

## 当前研究边界

- 股票池：历史时点（PIT）S&P 500成分股；long-only为主。
- 市场数据：2013年开始暖机，统一评价期为2018-01-02开盘至2026-06-30收盘。
- 数据状态：冻结免费研究数据通过内部门禁，但仍为 `formal_eligible=false`；完整行情和大体积运行产物保留在本地runtime。
- 横截面下一阶段：执行已冻结的XA04统一模型比较；完成并硬停后，再决定合格树与`XS003_MOM_12_7`的P00迁移。新闻和文本由独立任务研究。
- 实验方法：所有交易时点只能使用当时已知数据；失败、停止、无效和被替代路径均保留。
- 证据语言：完整历史walk-forward解决基本前视问题，但不自动消除研究者择优偏差；历史结果称研究证据，不冒充未来确认。

详细数据版本和PIT约束见[数据入口](docs/10_data/README.md)。

## 研究资产在哪里

| 内容 | 位置 |
|---|---|
| 两条主线与研究路线 | [`docs/research_tracks/`](docs/research_tracks/README.md) |
| 治理、数据、实验、参考资料 | [`docs/`](docs/README.md) |
| 论文和因子定义机器登记 | [`config/research/cross_sectional_alpha/`](config/research/cross_sectional_alpha/README.md) |
| 冻结实验配置与锁 | [`config/experiments/`](config/experiments/) |
| 因子、数据、回测与实验代码 | [`src/momentum_reversal/`](src/momentum_reversal/) |
| 运行、审计、发布和制图脚本 | [`scripts/`](scripts/) |
| 计划快照与执行台账 | [`experiments/`](experiments/README.md) |
| 紧凑机器结果 | [`results/published/`](results/published/README.md) |
| 图表 | [`docs/figures/`](docs/figures/README.md) |
| 冻结数据哈希和质量门 | [`metadata/frozen_dataset/`](metadata/frozen_dataset/) |

## 安装与离线测试

```powershell
python -m pip install -e ".[data]"
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

测试离线运行。API凭证只能放在被Git忽略的 `.env` 中。

## 本地runtime

大体积数据与完整实验bundle不应写入同步仓库。复制 `config/runtime.example.toml` 为Git忽略的 `config/runtime.local.toml`，并检查运行目录：

```powershell
python -m momentum_reversal runtime-status --create
```

运行区政策见[本地实验运行区规范](docs/10_data/runtime_storage_policy.md)。Git只保存代码、配置、哈希、质量摘要、紧凑结果和图表；daily NAV、持仓、交易、provider payload及完整价格文件保留在runtime。

## 审计约定

`experiments/*_registry.csv`通常是运行前预注册快照，不是实时状态页。已经执行的机器事实优先读取 `round*_results.csv`、published manifest/decision和对应报告；详见[实验台账说明](experiments/README.md)。冻结设计、锁、报告和历史结果不因文档整理而移动或改写。

本仓库是研究记录，不构成投资建议，也不声称免费研究数据具备生产级质量。
