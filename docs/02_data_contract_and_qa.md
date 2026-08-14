# 数据契约与质量保证（QA）

> 状态：数据接口合同 v1，2026-08-13  
> 适用包：`momentum_reversal`  
> 相关文档：[研究总计划](./00_research_plan.md) · [第一轮基线规格](./01_round1_baseline_spec.md) · [实验台账规范](./03_experiment_ledger.md)

## 1. 设计原则

1. 策略模块不依赖具体数据供应商；
2. 原始快照只追加和版本化，不覆盖；
3. 清洗后的字段和时点含义明确；
4. PIT 成分、价格和市场日历分别存储，通过稳定内部 `sid` 连接；
5. ticker 是供应商代码或展示字段，不作为跨历史的唯一主键；
6. 缺失数据显式失败或进入异常报告，绝不静默改变股票池；
7. 总回报复权价格处理分红、拆股等普通公司行动；并购、现金收购和换股等终止性事件使用独立、可审计的公司行动账本，不能伪装成 ticker alias。

## 2. 数据源优先级

### 2.1 原型 Provider：yfinance

用途：快速完成端到端框架和基线试跑，并与其他数据源交叉检查。

建议保留原始字段并记录下载参数，例如：

```python
auto_adjust=False
actions=True
repair=False
keepna=True
```

不将 `repair=True` 产生的数据覆盖原始快照；如需研究其修复效果，应创建新的 `dataset_version`。

`yfinance` 本身不提供完整 PIT S&P 500 历史成分，因此必须从独立、可追溯的成员文件输入。历史成员在 Yahoo 找不到数据时，不能从股票池静默移除。

### 2.2 正式 Provider：Norgate

用途：在同一体系中取得历史指数成分、稳定证券标识、退市证券和总回报价格，用于正式复跑。代码应将 Norgate `assetid` 映射为内部 `sid`，而不让策略模块直接依赖 ticker。

### 2.3 可选正式或复核 Provider：CRSP

如具备机构授权，可用 CRSP 作为正式源或对 Norgate 结果进行复核。不同 Provider 的结果必须拥有不同 `dataset_version`。

## 3. 目录与版本约定

建议项目数据布局：

```text
data/
  raw/
    yfinance/<snapshot_id>/
    norgate/<snapshot_id>/
    membership/<snapshot_id>/
    benchmarks/<snapshot_id>/
  curated/<dataset_version>/
  manifests/<dataset_version>.json
```

大表使用 Parquet，查询和研究可使用 DuckDB。原始数据、清洗数据和实验产物不可混放。

每个 `dataset_version` 的 manifest 至少记录：

- Provider 和授权/版本信息；
- 下载时间、请求参数与原始快照路径；
- 原始文件哈希；
- 成分数据来源及其“公告日/生效日”语义；
- 复权构造方法；
- 日历来源；
- 清洗代码版本或 Git commit；
- 覆盖日期、证券数、行数；
- 已知异常和人工映射；
- 父数据版本（若为修订版本）。

同一实验运行只能引用一个冻结的 `dataset_version`。

## 4. 复权价格契约

### 4.1 统一命名

中文资料对“前复权/后复权”的命名可能不一致。项目内部不依赖归一化方向，统一使用：

```text
tr_open, tr_high, tr_low, tr_close
```

它们表示同一总回报复权尺度上的 OHLC。只要收益率、拆股和现金分红的经济效果被正确保留，整条价格序列乘以任意正常数不应改变信号、排名或组合收益。

### 4.2 yfinance 转换

保留原始 OHLC 和 `Adj Close` 后，定义当日复权因子：

\[
a_t=\frac{AdjClose_t}{Close_t}
\]

并构造：

\[
tr\_open_t=Open_t\cdot a_t,\quad
tr\_high_t=High_t\cdot a_t,\quad
tr\_low_t=Low_t\cdot a_t,\quad
tr\_close_t=AdjClose_t
\]

该转换必须接受数据不变量测试。若 Provider 已原生提供同一尺度的 total-return-adjusted OHLC，直接映射字段并在 manifest 中说明。

### 4.3 使用边界

- 历史信号：只用 `tr_close`；
- 历史开盘成交和收益：使用同一尺度的 `tr_open/tr_close`；
- 不允许将原始 Open 与复权 Close 直接计算收益；
- 实盘映射：用复权数据计算排名和目标资金，再用当时原始报价换算实际股数；
- Volume 保持供应商原始或拆股调整口径，并明确单位；第一轮不使用 Volume 筛选。

## 5. 核心表契约

### 5.1 `security_master`

| 字段 | 类型 | 含义 |
|---|---|---|
| `sid` | string | 项目内稳定证券 ID，主键 |
| `provider` | string | 数据供应商 |
| `provider_sid` | string | 供应商稳定 ID；无则显式为空 |
| `ticker` | string | 对应记录时期的代码，仅作查询/展示 |
| `name` | string | 证券名称 |
| `valid_from` | date/null | 该 ticker 映射起点 |
| `valid_to` | date/null | 该 ticker 映射终点 |

第一轮不建设覆盖全部公司行动类型的通用引擎，但必须记录人工确认的 ticker 映射，并用终止性事件账本处理真实持仓会计，避免把不同证券误接。账本契约、复权单位转换和事件/调仓顺序见 [终止性公司行动账本与组合会计](./07_corporate_action_accounting.md)。

### 5.2 `membership`

| 字段 | 类型 | 含义 |
|---|---|---|
| `index_id` | string | 固定为 S&P 500 对应 ID |
| `sid` | string | 内部证券 ID |
| `effective_from` | date | 纳入生效日，含当日 |
| `effective_to` | date/null | 剔除生效边界；推荐半开区间右端 |
| `source` | string | 成分来源 |
| `source_record_id` | string/null | 可追溯记录 ID |

统一采用半开区间 `[effective_from, effective_to)`。信号日在该区间内的证券拥有参选资格。若来源只有“某日期榜单快照”，先保存原始快照，再由明确、版本化的规则推导区间。

### 5.3 `prices_daily`

| 字段 | 类型 | 含义 |
|---|---|---|
| `sid` | string | 内部证券 ID |
| `date` | date | 交易会话日期 |
| `tr_open/high/low/close` | float | 同尺度总回报复权 OHLC |
| `raw_open/high/low/close` | float/null | 原始 OHLC，便于审计和实盘映射 |
| `volume` | float/null | 成交量及其口径见 manifest |
| `source` | string | Provider |
| `snapshot_id` | string | 原始快照版本 |

主键为 `(sid, date)`；不允许重复。

### 5.4 `calendar`

| 字段 | 类型 | 含义 |
|---|---|---|
| `session_date` | date | 市场交易日 |
| `week_last_session` | boolean | 当周最后交易日 |
| `month_last_session` | boolean | 当月最后交易日 |
| `next_session` | date | 下一交易日 |

调仓时点必须由会话日历派生，不能硬编码星期五和星期一。

### 5.5 `benchmark_daily`

| 字段 | 类型 | 含义 |
|---|---|---|
| `date` | date | 交易日 |
| `spxtr` | float/null | S&P 500 Total Return 指数 |
| `spy_tr` | float/null | SPY 总回报序列 |
| `rf` | float/null | 与绩效周期匹配的无风险收益率 |

### 5.6 `universe_at_signal`

该表可以按运行生成，用于审计而非代替原始 membership：

| 字段 | 含义 |
|---|---|
| `signal_date, sid` | 联合键 |
| `is_member` | 信号日是否为成员 |
| `has_signal_history` | 是否具备该信号所需历史 |
| `eligible` | 是否进入本次排名 |
| `exclusion_reason` | 明确排除原因 |
| `score, rank` | 得分和排名；无资格时为空 |

## 6. 数据获取范围

对每一只曾经可能参选的历史成分股，价格获取起点至少早于首次可能参选日期 300 个交易日，以覆盖 255 日形成期和日历差异。加入指数之前的公开价格历史允许用于动量计算。

正式回测起点由以下条件共同决定并写入 manifest：

- PIT 成分覆盖已达到研究要求；
- 所需形成期已经完整；
- 股票价格覆盖达到 QA 门槛；
- 基准和市场日历可对齐。

不能为了延长样本而用今天的成分股回填早期股票池。

## 7. 清洗规则

第一轮采用保守、可审计的清洗：

- 不前向填充个股价格；
- 不把缺失收益自动设为 0；
- 不因下一日缺少 Open 而在信号日提前排除股票；
- 不自动 winsorize、截尾或删除大幅涨跌；
- 不用 `repair=True` 结果覆盖原始快照；
- 普通分红、拆股依赖总回报复权数据，不重复进行现金流调整；
- 异常只标记，是否修复必须产生新的数据版本并保留来源；
- 所有日期转换到一致的交易所会话日期，不能因 UTC/本地时区导致错日。

## 8. QA 检查

### 8.1 结构完整性

- `(sid, date)` 唯一；
- `membership` 区间无非法重叠、倒置或零长度；
- `calendar.next_session` 严格向后且存在；
- 所有成员 `sid` 都可连接到 `security_master`；
- 所有实验引用的数据版本都存在 manifest 和原始快照哈希。

### 8.2 价格检查

- OHLC 为有限正数；
- `tr_high >= max(tr_open, tr_close)`；
- `tr_low <= min(tr_open, tr_close)`；
- 同一行 `tr_*` 使用一致复权尺度；
- 复权因子异常跳变、极端单日收益和长缺口进入报告，但不自动删除；
- 原始和复权字段的转换关系在浮点容差内成立。

### 8.3 PIT 与覆盖率检查

每个信号日至少输出：

- PIT 成分数量；
- 各信号具备完整历史的数量和比例；
- 缺失证券及原因；
- Top K 是否全部来自该日成员；
- 下一执行日入选证券 Open 覆盖情况。

历史成员价格缺失不能只显示较低覆盖率后继续正式发布结果；必须被修复、解释，或将运行标记为 `invalid_data`。

### 8.4 防前视测试

- 修改信号日之后的价格，不得改变该信号日分数和 Top K；
- 修改未来 membership，不得改变过去股票池；
- 新成员只能从 `effective_from` 开始参选，但可使用此前公开价格形成信号；
- 下一执行日 Open 缺失不能改变信号日排名；
- 形成期端点严格不晚于信号日允许时点。

### 8.5 回测不变量测试

- 整条复权价格乘任意正常数，分数、排名和收益不变；
- 同一信号同一日期满足 Top 10 ⊆ Top 20 ⊆ Top 50；
- 调仓后权重和为 1，且入选证券各为 `1/K`；
- 成分在两个调仓点间退出，不产生计划外交易；
- 0 bps 净值等于 gross；
- 对相同持仓路径，提高非负成本率不能提高最终净值；
- 节假日正确执行“最后交易日收盘 -> 下一交易日开盘”；
- 策略与基准起止日期完全一致。

## 9. 数据异常处理等级

| 等级 | 示例 | 处理 |
|---|---|---|
| `warning` | 非入选成员偶发缺少非必要字段 | 记录，可继续 |
| `review` | 极端收益、成员数异常、人工 ticker 映射 | 人工确认后产生说明 |
| `invalid_data` | 入选证券执行 Open 缺失、形成期错误、PIT 成分无法追溯 | 正式运行无效，修复或换数据版本 |
| `fatal` | 主键重复、日期错位、manifest 不存在 | 立即停止 |

修复后不得覆盖原运行：创建新的 `dataset_version` 和 `run_id`，并在台账中链接原异常。

## 10. 第一轮数据验收标准

开始发布 18 条正式基线结果前，必须满足：

1. 所有数据表通过结构 QA；
2. PIT 成分来源、时点语义和覆盖日期写入 manifest；
3. 每个信号日的成员数与价格覆盖报告可生成；
4. 入选证券在全部执行时点都有可审计的执行价格；
5. 防前视和回测不变量测试全部通过；
6. 任何人工映射、缺口或异常均在数据版本中留痕；
7. 一次运行可由 `dataset_version + config + code commit` 重现。
