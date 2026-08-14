# 实验台账与可复现性规范

> 状态：研究治理合同 v1，2026-08-13  
> 适用项目：`momentum_reversal`  
> 相关文档：[研究总计划](./00_research_plan.md) · [第一轮基线规格](./01_round1_baseline_spec.md) · [数据契约与 QA](./02_data_contract_and_qa.md)

## 1. 目的

实验台账用于回答四个问题：

1. 运行前原本打算检验什么？
2. 使用了哪一版数据、代码和配置？
3. 运行中是否偏离了预登记规则？
4. 成功、失败和无效实验分别产生了什么证据？

任何回测结果如果不能由台账定位到数据版本、完整配置和代码版本，就不能作为正式研究证据。

## 2. 实验、路径和成本情景

项目采用以下层级：

```text
batch（研究批次/机制族）
  experiment（一个预登记参数组合和交易路径）
    scenario（对同一路径重算的成本或报告情景）
      run（一次具体代码执行）
```

第一轮示例：

- `mom_255_0 + top10 + weekly` 是 1 个 experiment；
- 同一路径的 0/5/10/20 bps 是多个 scenario，不是 4 个 experiment；
- 更换数据源、代码或配置后重新执行，形成新的 run；
- 修改信号定义、Top K、频率或组合构建规则，形成新的 experiment。

## 3. ID 约定

建议采用稳定、可读且不复用的 ID：

```text
batch_id:      B01_MOM_BASELINE
experiment_id: B01_MOM255_0_TOP10_W
run_id:        B01_MOM255_0_TOP10_W__20260813T153000__a1b2c3d4
scenario_id:   COST_10BPS
```

ID 一旦出现在结果中不得修改或复用。展示名称可以优化，但必须保留原始 ID。

第一轮 18 个 `experiment_id` 由以下模板生成：

```text
B01_{MOM255_0|MOM255_21|MOM12_1CAL}_{TOP10|TOP20|TOP50}_{W|M}
```

## 4. 预登记字段

每个 experiment 在第一次运行前至少登记：

| 字段 | 含义 |
|---|---|
| `experiment_id` | 不可复用的稳定 ID |
| `batch_id` | 所属批次 |
| `title` | 简短名称 |
| `status` | 当前状态 |
| `registered_at` | 预登记时间 |
| `hypothesis` | 该路径检验的经济假设 |
| `signal_name` | 精确定义的信号名称 |
| `signal_params` | 形成期、跳过期及端点规则 |
| `universe` | PIT S&P 500 及资格规则 |
| `portfolio` | long-only、Top K、等权、满仓等 |
| `rebalance` | 信号/执行时点和频率 |
| `execution` | 开盘成交、分数股等假设 |
| `cost_scenarios` | 主成本与敏感性成本 |
| `benchmark` | SPXTR、SPY TR 等 |
| `metrics` | 预定输出指标 |
| `dataset_requirement` | 数据覆盖和 Provider 要求 |
| `parent_experiment_id` | 若为扩展或修订，指向父实验 |
| `notes` | 运行前补充说明 |

参数必须存为机器可读配置；Markdown 台账可以摘要和链接，但不能是唯一参数来源。

## 5. 状态机

推荐状态：

```text
planned -> running -> completed
                  \-> failed_runtime
                  \-> invalid_data
                  \-> invalid_method
planned -> cancelled
```

含义：

- `planned`：已预登记，尚未运行；
- `running`：正在运行；
- `completed`：按预登记规则完成，不代表策略表现合格；
- `failed_runtime`：代码或环境错误，仍保留日志；
- `invalid_data`：数据缺口或时点问题使结果不能使用；
- `invalid_method`：实现偏离规格或发生前视；
- `cancelled`：运行前取消，并记录原因。

策略未超过 SPY、Sharpe 不足 1 或回撤超过 25%，仍然是 `completed`，不是 `failed_runtime` 或 `invalid`。其经济评价另设 `assessment` 字段，例如 `meets_gate`、`below_gate`、`diagnostic_only`。

## 6. 每次 run 的不可缺失信息

每次具体执行生成不可变 manifest，至少包括：

```yaml
run_id: B01_MOM255_0_TOP10_W__20260813T153000__a1b2c3d4
experiment_id: B01_MOM255_0_TOP10_W
started_at: 2026-08-13T15:30:00+08:00
finished_at: null
status: running
dataset_version: yf_pit_v001
config_hash: sha256:...
git_commit: a1b2c3d4...
working_tree_dirty: false
python_version: ...
dependency_lock_hash: sha256:...
random_seed: null
date_start: null
date_end: null
artifact_root: runs/...
```

第一轮没有随机模型，因此 `random_seed` 可为空；未来含随机过程时必须固定并记录种子。

如果工作树存在未提交改动，应记录 patch 哈希或将正式运行标记为不可复现，不能只记录 Git commit。

## 7. 推荐产物结构

```text
configs/
  batches/B01_MOM_BASELINE.yaml
  experiments/<experiment_id>.yaml
experiments/
  registry.csv
runs/<run_id>/
  manifest.json
  config_resolved.yaml
  data_qa.json
  logs.txt
  signals.parquet
  rankings.parquet
  holdings.parquet
  trades.parquet
  nav_gross.parquet
  nav_net.parquet
  metrics.json
  summary.md
results/
  B01_MOM_BASELINE/
    comparison.parquet
    comparison.csv
    report.md
```

`config_resolved.yaml` 必须包含继承和默认值展开后的最终配置，避免以后默认值变化而无法复现。

## 8. 第一轮参数登记示例

批次级固定配置示例：

```yaml
batch_id: B01_MOM_BASELINE
universe:
  index: sp500
  point_in_time: true
  membership_asof: signal_close
portfolio:
  side: long_only
  weighting: equal
  fully_invested: true
  leverage: 1.0
  rebalance_to_equal_weight: true
execution:
  timing: next_session_open
  fractional_shares: true
costs:
  turnover: double_sided_l1
  weekly:
    primary_bps: 10
    sensitivity_bps: [0, 5, 20]
  monthly:
    primary_bps: 5
    sensitivity_bps: [0, 10, 20]
benchmarks: [spx_total_return, spy_total_return]
```

实验级变化项示例：

```yaml
experiment_id: B01_MOM255_21_TOP20_W
batch_id: B01_MOM_BASELINE
signal:
  name: mom_255_21
  lookback_sessions: 255
  skip_recent_sessions: 21
portfolio:
  top_n: 20
rebalance:
  frequency: weekly
  signal_timing: last_session_close
  execution_timing: next_session_open
```

## 9. 偏差、修订和重跑

运行后发现规则或数据问题时：

1. 原 run 保持不可变；
2. 在 manifest 标记 `failed_runtime`、`invalid_data` 或 `invalid_method`；
3. 记录具体问题、发现时间和影响范围；
4. 数据修复创建新 `dataset_version`；
5. 规则或参数变化创建新 experiment；
6. 仅代码 bug 修复、规格未变时，可以在同一 experiment 下创建新 run；
7. 新 run 用 `supersedes_run_id` 链接旧 run，但不能删除旧产物。

禁止为了改善历史结果而：

- 修改异常值处理后覆盖旧数据；
- 只保留最佳成本、起止日期或 Top K；
- 将事后新增参数伪装成原批次的预登记候选；
- 删除未达评价门槛的完整实验。

## 10. 批次完成报告

批次报告必须覆盖该批次全部预登记 experiment，并至少包含：

- 计划数量、完成数量、无效数量和取消数量；
- 数据版本、代码版本和共同配置；
- 逐实验 gross/net 指标表；
- 相对 SPXTR/SPY 的比较；
- 成本敏感性；
- 换手、集中度和贡献归因；
- 年度和滚动期稳定性；
- 最好与最差路径，以及失败原因分类；
- 所有运行偏差和数据异常；
- 是否满足研究门槛，但不得据此删除其他路径；
- 下一批实验建议，明确哪些是假设驱动、哪些是结果驱动。

## 11. 策略选择纪律

无固定验证集时，台账本身承担重要的研究治理职责：

- 参数网格在运行前冻结；
- 每个参数组合视为独立试验；
- 成本情景不冒充新试验；
- 报告参数稳定区间，不只报告最高 Sharpe；
- 记录累计尝试次数，后续进行多重检验或 Deflated Sharpe 诊断；
- 规则冻结后的 paper/live forward 结果与历史回测分栏报告。

“参数组合数量有限”可以降低但不能消除事后选择偏差，因此任何实盘候选都应基于机制解释、跨时期稳定性、成本后表现和集中风险共同决定。

