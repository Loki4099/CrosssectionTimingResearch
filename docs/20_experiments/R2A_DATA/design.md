# R2A_DATA：第二轮防御择时数据扩展——冻结设计

状态：**设计已冻结；尚未获取或构建 R2A 数据。** 本文只授权数据获取、标准化、QA 与不可变冻结；在 R2A 数据 manifest 通过全部门禁前，禁止计算 T1/T2/T3、生成特征表现、训练模型、回测策略或查看任何候选的经济结果。

关联计划：[第二轮防御启动信号计划 v1](../../23_round2_defense_timing_signal_program_v1.md)。R2A 不修改第一轮冻结 v3 数据、任何 G 组 bundle、历史报告或已发布证据。

## 1. 目标与边界

R2A 建立两条相互独立的数据线：

| 数据线 | 候选版本 | 用途 | 目标范围 |
|---|---|---|---|
| L：长样本市场核心线 | `round2-market-core-1993-2026-v1-candidate` | R2B/R2C 的核心信号、标签、SPY/T-bill 会计 | 1993-01-29 至 2026-06-30 |
| S：PIT 成分股增量线 | `round2-sp500-pit-norgate-1990-2026-v1-candidate` | 价格广度、方向性成交量广度及 R2D 的 `mom_255_0` 转移 | 最早 1990，正式共同期不早于 SPY 起点 |
| R2A 组合索引 | `round2-defense-timing-r2a-v1-candidate` | 只引用已冻结 L/S manifest，不复制数据 | 由合格子线决定 |

L 线可以独立完成并授权 long-core 实验。S 线没有通过许可、永久标识、历史成分和成交量门禁时，必须保持 `blocked`；不得以现代成分回填、Yahoo 当前 ticker、零值、均值或 L 线变量代替。

本次冻结截止日固定为 2026-06-30。不得因为当前供应商后来增加或修订数据而在原版本上追加；任何刷新必须创建新 snapshot 和新 dataset version。

## 2. 冻结供应商与角色

### 2.1 L 线

1. **SPY：Tiingo EOD。** 使用 Tiingo 原生 raw OHLCV、adjusted OHLC/volume、现金分红与拆股字段。项目已有凭据隔离和 EOD adapter；token 只可从本地环境或被 Git 忽略的 `.env` 读取，不得写入日志、manifest、异常、命令行或仓库。
2. **VIX：Cboe 官方历史日线。** 只使用 VIX 日收盘；它是可选 F3 数据块，不是可交易资产。若 1993 起点后的日历覆盖、日期唯一性或源快照门禁失败，F3 固定为 `invalid_data/not_available`，不能换第三方源救场。
3. **现金/RF：Kenneth R. French Data Library 日频因子中的 RF。** RF 只用于实现现金收益及 T1/T3 outcome；不作为同日可见模型特征。保存下载快照、文件哈希、单位与资料库方法版本。资料库方法或 RF 来源变更必须形成显式分段和 provenance，不能被平滑成同一未说明序列。
4. **交易日历：XNYS。** 用项目锁定依赖生成权威 session；不得用 SPY 实际有价日期反推日历。周频信号为每个 W-FRI 周期最后一个 XNYS session 收盘，执行为下一 XNYS session 开盘。

Yahoo 只允许作为重叠期的非正式差异诊断，不得在 Tiingo 缺口处拼接。SPX、合成开盘、当前 S&P 500 成分或未调整价格不得补到 SPY 成立前。

### 2.2 S 线

S 线冻结主源为 **Norgate Data US Stocks Platinum 或更高且包含 Historical Index Constituents 的订阅**：

- 使用供应商永久证券标识、在库/退市证券历史、S&P 500 历史成员资格、日线 OHLCV 与公司行动；
- 通过供应商 Python/本地数据库接口按 session 查询成员资格，再转换为项目内部半开区间 `[effective_from, effective_to)`；
- 不要求或伪造供应商不提供的“原始成分名单文件”；每次查询保存请求配置、软件/数据库版本、抽取时间、输出哈希与成员计数审计；
- 股票原始数据与成员明细只保存在本地 runtime。Git 只保存代码、字段合同、聚合 QA、manifest 与哈希；任何分发必须先满足实际订阅许可。

Norgate 订阅、Windows Updater/Python 接口和本地数据库尚未就绪时，S 线状态必须为 `blocked_external_dependency`。用户授权研究数据扩展不等于授权代为购买订阅，也不允许将账号凭据写入项目。

## 3. 许可与可复现性

每个源必须在 `source_ledger.json` 保存：供应商、产品/数据集名称、官方说明链接、订阅层级、许可审阅状态、允许的本地研究用途、禁止分发项、请求参数、下载/抽取 UTC 时间、源文件逐项 SHA256、客户端版本和人工备注。

硬规则：

- `license_review_status != approved_for_local_research` 时对应分支不得进入 curated；
- 原始授权数据不得提交 Git，不得复制到 OneDrive repo；只写本地 runtime 的 immutable staging 目录；
- API token、用户名、密码、许可证文件、数据库路径和供应商个人标识不得进入 manifest；manifest 只记录环境变量名称和匿名化 source ID；
- 相同 source snapshot、代码和配置重复构建，所有 curated 表的 schema、行数、排序后内容哈希必须一致；
- 供应商的历史数据可能发生修订。新下载即使请求区间相同，也必须使用新 snapshot ID 并与父版本逐日 diff。

## 4. 原始字段合同

### 4.1 SPY `market_daily`

每个 XNYS session 恰一行，至少包含：

```text
session_date, asset_id, raw_open, raw_high, raw_low, raw_close,
tr_open, tr_high, tr_low, tr_close,
volume_raw, volume_adjusted, dividend_cash, split_factor,
provider, provider_symbol, source_snapshot_id, available_at,
source_record_hash
```

Tiingo 映射固定如下：

| 项目字段 | Tiingo 字段 |
|---|---|
| `raw_open/high/low/close` | `open/high/low/close` |
| `tr_open/high/low/close` | `adjOpen/adjHigh/adjLow/adjClose` |
| `volume_raw` | `volume` |
| `volume_adjusted` | `adjVolume` |
| `dividend_cash` | `divCash` |
| `split_factor` | `splitFactor` |

普通分红和拆股已经反映在 adjusted OHLC 中，NAV、标签和收益只使用 `tr_*`，不得再次把 `dividend_cash` 或 `split_factor` 加到账上。raw 字段只用于价格/成交量 QA 和可交易价格诊断；R2C 的 SPY/T-bill 会计使用 `tr_open/tr_close`，与第一轮冻结代理口径保持一致。

`available_at` 对日线收盘信号固定为该 XNYS session 官方收盘时刻之后；实现必须使用时区感知的 `America/New_York` 时间戳。供应商抓取时间另存为 `retrieved_at`，不得把历史抓取时间冒充当年信息可得时间。

### 4.2 VIX `vix_daily`

```text
session_date, vix_close_percent, provider='CBOE',
source_snapshot_id, available_at, source_record_hash
```

`vix_close_percent` 必须有限、非负；F3 使用时转换为小数并按计划公式计算 `(vix_close_percent/100)^2 - spy_rv21^2`。不得混用百分数和小数，也不得把缺失 VIX 前填至下一信号。

### 4.3 `risk_free_daily`

```text
session_date, rf_percent_source, rf_simple_decimal, rf_log,
source, methodology_segment, source_snapshot_id,
realized_available_at, retrieved_at, source_record_hash
```

`rf_simple_decimal = rf_percent_source / 100`，`rf_log = log1p(rf_simple_decimal)`。结果标签和现金 NAV 可以使用事后实现 RF；特征不得读取同日 RF。由于 French 历史文件没有逐日发布时刻档案，R2A 对研究代理固定采用保守可用性约定：session `d` 的 realized RF 在下一 XNYS session 开盘才视为可用，并将 `availability_policy = next_xnys_open_research_proxy` 写入每行与 manifest；不得事后缩短。缺失值不得填零或前填。French 资料库方法/源切换要进入 `methodology_segment`，并在切点前后报告分布差异。

### 4.4 `decision_calendar`

```text
week_id, signal_session, signal_timestamp_et,
execution_session, execution_timestamp_et,
next_1w_execution, next_4w_execution,
signal_weekday, execution_weekday, holiday_flags,
calendar_package_version, calendar_hash
```

本表只由 XNYS 日历和冻结截止日生成，不读取未来收益或模型结果。信号/执行路径的所有价格端点和 T3 中间 session 必须能由该表追溯。

### 4.5 S 线证券与成员表

`security_master` 至少包含供应商永久 ID、项目 canonical SID、ticker/name 的有效区间、交易所、首末交易日、退市状态及 mapping provenance。`membership_intervals` 至少包含 index ID、canonical SID、`effective_from`、`effective_to_exclusive`、query snapshot 和 row hash。

同一 `(index_id, sid)` 区间不得重叠；同一 session 不得由未来 ticker 或现代成员状态回填。成员状态按 signal session 收盘已生效的区间计算。

### 4.6 S 线股票日线与广度输入

股票日线保存 raw 与 total-return-adjusted OHLC、raw/adjusted volume、普通公司行动和供应商永久 ID。信号、估值和普通分红/拆股会计只使用同一套 adjusted OHLC；显式公司行动只允许覆盖供应商 total-return 字段无法表达且已冻结的 terminal event，并必须有 no-double-count gate。

F4 有效掩码为：

```text
PIT member & required adjusted prices finite and positive & not masked-untradable
```

F5 有效掩码为：

```text
PIT member & required raw/adjusted prices finite and positive
& volume finite and positive & not masked-untradable
& split/provider volume policy valid
```

成交量特征必须先在每只证券自身历史中严格滞后标准化，再按当日 PIT 成员聚合。禁止直接对全市场 raw shares/dollar volume 求和，禁止用未来 shares outstanding，禁止把未验证的 volume 当作 turnover。

## 5. 调整、排序与哈希

1. 原始响应按供应商原始顺序完整保存；curated 表统一按主键稳定排序后写 Parquet。
2. 时间戳、浮点类型、字符串 normalization、Parquet writer 与压缩参数在 machine config 中固定；内容哈希以排序后 Arrow 表的规范 IPC bytes 为主，文件 bytes SHA256 为辅。
3. manifest 记录每个文件的相对路径、schema、row count、bytes、file SHA256、canonical-content SHA256、最小/最大日期、null count 和唯一键状态。
4. `FROZEN.json` 只在全部 required gates 通过后创建，包含 dataset/version、父版本、设计 SHA、配置 SHA、代码 commit、依赖锁、source ledger SHA 和 manifest SHA。
5. 同 version 目录一旦存在即拒绝覆盖；任何失败构建写入新的 staging run，不得留下伪 completed manifest。

## 6. L 线硬门禁

必须全部通过：

1. Tiingo、Cboe、French 和 XNYS source ledger 完整，许可状态可用于本地研究；
2. SPY 从供应商核验的首个可交易 session 起至 2026-06-30，每个 XNYS session 的 adjusted open/close 100% 覆盖；OHLC 全部有限正、关系合法、主键唯一；
3. 所有周信号、执行开盘、T1 端点、T3 全部中间收盘与终点开盘 100% 有价；对应 RF sessions 100% 覆盖；
4. adjusted close-to-close、open-to-close、open-to-open 收益有限且大于 -100%；任何极端值只可审计，不可静默 winsorize；
5. raw/adjusted 字段的分红拆股缩放关系逐事件审计；调整后收益与现金流不得双计；
6. VIX 共同 XNYS 日的日期唯一、有限非负；缺失周导致 F3 整个 arm invalid，不可补值；
7. 与当前冻结 v3 的 2013-01-02 至 2026-06-30 SPY/RF 重叠期逐日 reconcile。相同 provider snapshot 应逐位一致；不同 snapshot 的差异必须列出日期、字段、绝对/相对差和供应商修订说明；
8. 修改任一信号日之后的原始记录不得改变此前 calendar、curated values 或未来无关表；
9. 两次独立 clean build 的 curated schema、行数和 canonical-content hashes 完全相同；
10. 由实际冻结日历生成首个 feature-complete signal、520 周门槛、outer/inner 候选折和 lockbox 候选日期，但本阶段不得读取或计算 target 数值。

任一 required gate 失败，L 线状态为 `invalid_data/fatal`，R2B/R2C 不得启动。

## 7. S 线硬门禁

S 线除通用门禁外还必须满足：

1. Norgate 产品与许可经人工确认，客户端、数据库版本和历史成分功能可复现；
2. 所有历史/退市证券有永久 provider ID；ticker 映射按有效期唯一，ticker 重用不得合并；
3. S&P 500 membership 对每个 session 可复算，区间无重叠、无倒序、无现代成员回填；成员数量突变必须有来源记录；
4. 每个进入模型的信号日 F4/F5 联合覆盖率均不低于 98%，全期中位数均不低于 99%；任一缺分周在 paired core 与 PIT treatment 两侧同时 carry-forward/no overlay rebalance，不能压缩日期；连续缺分超过 4 周或总缺分超过 2% 时整条 paired bundle invalid；
5. split 前后 raw/adjusted price-volume 关系、跨供应商单位及 terminal events 全部通过；任何一项不通过时 F5 整块 blocked，而 F4 可独立保留；
6. 至少抽查每年首/末 session、所有成员数异常日、所有 ticker/永久 ID 变更和全部 terminal events；
7. 与现有 v3 共同期只作差异审计，不要求错误的免费来源逐位相等；必须报告成员交集、价格差异、缺失和原因，不可择优拼接；
8. 在全局 lockbox 起点前是否拥有 520 个 label-mature 训练周和至少 3 个完整 development outer 年，只由冻结 calendar/coverage mask 判定；本阶段不计算 label 值。资格不足则固定为 `exploratory/no_champion_eligibility`。

## 8. 可接受缺口与失败策略

- L 线 SPY、RF、周历和所有 target 路径端点：**不接受缺口**。
- VIX：不允许前填或替代源；不合格只取消 F3，不阻断其余 L 线。
- S 线价格/成交量：只接受第 7 节预先规定的 coverage/carry-forward 处理；不得删除中间收益区间。
- 供应商临时下载失败：可在同一 staging run 内按固定退避重试；最终源响应、重试次数和失败必须进入 ledger。不得用另一个供应商静默补行。
- Norgate 未购买、未安装或许可未批准：S 线保持 blocked，不影响 L 线构建；不得因此降低 PIT 的历史或覆盖门槛。

## 9. 输出合同

L 线 completed candidate 必须至少包含：

```text
raw/source_ledger.json
raw/download_manifest.json
curated/market_daily.parquet
curated/vix_daily.parquet
curated/risk_free_daily.parquet
curated/decision_calendar.parquet
qa/gates.json
qa/overlap_reconciliation.parquet
qa/adjustment_events.parquet
manifest.json
FROZEN.json
```

S 线 completed candidate 必须至少包含：

```text
raw/source_ledger.json
raw/extraction_manifest.json
curated/security_master.parquet
curated/membership_intervals.parquet
curated/prices_daily.parquet
curated/breadth_input_coverage.parquet
qa/gates.json
qa/membership_audit.parquet
qa/price_volume_adjustment_audit.parquet
qa/overlap_reconciliation.parquet
manifest.json
FROZEN.json
```

`features_weekly`、`targets_weekly`、模型预测、NAV 和报告图不属于 R2A 输出；若出现在数据目录中，R2A 必须判 invalid。

## 10. 执行顺序与当前 readiness

1. 冻结本文和 machine config；
2. 审阅实际供应商许可，不接触 target；
3. 实现/测试 Tiingo `adjVolume`、Cboe、French 长历史与规范哈希；
4. 构建并双跑 L 线 staging，完成重叠 reconciliation；
5. 通过全部 L 线门禁后创建 immutable candidate/FROZEN；
6. 在用户单独完成 Norgate 订阅与本地安装后，构建 S 线；若外部依赖未就绪，明确保留 blocked；
7. 只从已冻结 manifest 生成 R2B/R2C machine preregistration 的日历与绝对日期，不提前解封 target。

当前本地 readiness（2026-08-16）：

- 项目 `.env` 存在，Tiingo adapter 可从被忽略的本地文件解析 `TIINGO_API_TOKEN`；本设计未读取或输出 token；
- `exchange_calendars` 与 `pyarrow` 已安装；
- `norgatedata` Python 包、本地数据库路径和账号环境变量尚不可用；
- 因此 L 线可进入实现/采集，S 线当前为 `blocked_external_dependency`。

## 11. 官方来源

- Tiingo EOD 字段与调整口径：[End-of-Day API Documentation](https://www.tiingo.com/documentation/end-of-day)
- Cboe VIX 官方历史：[VIX Historical Price Data](https://www.cboe.com/tradable_products/vix/vix_historical_data)
- Kenneth R. French 数据库：[Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library.html) 与 [Fama/French Factors description](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library/f-f_factors.html)
- Norgate 数据范围：[Data Content Tables](https://norgatedata.com/data-content-tables.php)、[Stock Market Packages](https://norgatedata.com/stockmarketpackages.php)、[Data Package FAQ](https://norgatedata.com/data-package-faq.php) 与 [Python/accessibility](https://norgatedata.com/accessibility.php)

## 12. 冻结规则

本文冻结后，只允许不改变数据语义的拼写/链接修复。供应商、字段、日期、adjustment、coverage、哈希、许可或失败策略的任何变化，都必须新建 R2A design 版本并在数据获取前冻结。R2A 结果不得用于反向修改本文。
