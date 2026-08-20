# 横截面市场与SEC基本面源数据库 v1

状态：**源数据获取、PIT处理与研究级认证完成；`formal_eligible=false`。** 本文不是因子库、因子实验计划或实验结果。

## 范围

本版本在不修改既有冻结市场数据的前提下，建立可重复使用的横截面源数据层：

1. 将历史 `sid` 连接到有效期化的发行人与SEC CIK；
2. 保存SEC原始响应、下载账本、申报事件和数值事实的不可变快照；
3. 严格按SEC接受时间构建point-in-time年度规范事实；
4. 独立重算身份覆盖、会计恒等式、来源适用性和历史实体支持；
5. 为后续因子计算提供统一输入，但本轮不计算或认证任何因子。

正式配置见[`data_program.toml`](../../config/research/cross_sectional_alpha/data_program.toml)，紧凑认证证据见[`results/published/cross_sectional_data/xs-market-sec-source-data-20260820-v1`](../../results/published/cross_sectional_data/xs-market-sec-source-data-20260820-v1/)。

## 已认证的数据边界

| 层级 | 认证结果 |
|---|---|
| 冻结市场父版本 | `sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate` 的manifest、质量锚和10个curated成员逐字节通过 |
| 证券身份 | 745/745 SID完成映射；772个有效期CIK区间；1,701,149/1,701,149成员交易日覆盖 |
| SEC处理范围 | 753/753研究期CIK完成；聚合失败0 |
| 申报与事实 | 2,386,818条filing、1,679,666条注册事实、358,303条规范年度事实 |
| 原始证据 | 2,885条下载记录、2,770个唯一原始对象、252,770,109字节逐对象size/SHA验证，object store与账本精确相等 |
| 会计恒等式 | 29,146个上下文；8,570个直接完整上下文全部通过，直接失败0；安全合成COGS上下文2,363个均保留provenance |
| 历史实体支持 | 766个研究期区间，时序失败0；权威输入独立重算与发布QA逐值相等 |

751个CIK的Company Facts来源可用。FRC（`0001132979`）与SBNY（`0001288784`）只有在官方Company Facts返回精确`NoSuchKey`、submissions中不存在发行人定期/XBRL申报且原始响应哈希通过时，才被闭集白名单标记为`resolved_not_applicable`；二者没有插补任何事实。历史HTTP失败记录继续保留在不可变账本中，不会被删除或伪装成成功。

## PIT与证据规则

- 申报以accession事件保存，并由submissions ledger中的form与accepted timestamp提供权威可得性；
- `10-K/A`只从自身接受时刻起形成新vintage；后来申报重列的旧期间数值不能覆盖过去；
- period end晚于accepted/available session的事实保留作审计，但禁止进入规范输入；
- 第一版规范事实只使用年度`10-K`与`10-K/A`，不混入10-Q YTD转单季问题；
- 当前SIC不能回灌历史；没有可验证PIT行业分类时，只记录适用性未知，不冒充历史行业过滤；
- 缺失字段不补零；单位、期间、维度或表单口径无法安全调和时，结果保持缺失；
- Company Facts孤儿accession只有在全部核心观察与唯一ledger accession严格同值时才可保守别名解析，否则fail closed；
- 任何新版本必须重新验证原始对象、逐CIK manifest、aggregate manifest和`FROZEN.json`锚。

## 证据与查询层

- 原始响应、fetch ledger、Parquet和manifest是源数据证据；
- DuckDB只是可从manifest与Parquet确定性重建的本地查询索引，不是source of truth；
- 基本面版本只引用冻结市场父版本，不复制或改写`prices_daily.parquet`；
- 大型原始数据、完整事实表与数据库留在runtime，Git仅发布固定白名单的JSON/CSV/README认证摘要；
- source-only认证不导入收益标签，不读取因子结果，也不授权实验。

认证证书锚定了整份`data_program.toml`。后续因子阶段若需要改变因子配置，应发布新的program/version并引用本源层，而不是原地改写本v1配置或让既有证书失效。

主要runtime工件如下：

| 工件 | 粒度/用途 |
|---|---|
| `entity_bridge.parquet` | SID与发行人/CIK候选及审阅provenance |
| `entity_cik_intervals.parquet` | SID × CIK半开有效区间 |
| `filings.parquet` | accession级申报与可得时点 |
| `registered_facts.parquet` | 注册指标的as-filed观察与完整来源字段 |
| `canonical_annual_facts.parquet` | 只由合法年度vintage形成的规范事实 |
| `source_applicability.parquet` | 每个研究期CIK的来源适用性与例外证据 |
| `accounting_identity_qa.parquet` | Revenue、COGS与Gross Profit口径调和审计 |
| `entity_temporal_support_qa.parquet` | 每个历史实体区间的申报支持审计 |
| `manifest.json` / `FROZEN.json` | 内容哈希、构建签名和冻结状态 |

## 本轮未完成、不得误读的范围

`factor_definition_registry.csv`、`active_factor_registry.csv`及相关因子代码只是后续工作的候选设计或实现脚手架。本轮不对任何`factor_id`宣称：

- 已计算或已写入统一因子面板；
- 覆盖率、相关性或经济表现合格；
- 可进入“第一轮”实验；
- 已获得模型、回测、P00协同或任何交易实验授权。

BM、净派现收益率、严格Sloan应计等定义仍需在因子阶段分别通过股份类别市值、字段覆盖和适用性门；不能因为源数据认证通过而自动晋级。新闻、分析师、期权、供应链和其他替代数据不属于本数据库版本。

## 复核入口

source-only认证器是[`scripts/certify_cross_sectional_source_data.py`](../../scripts/certify_cross_sectional_source_data.py)。它逐字节验证冻结市场父锚、身份映射、SEC原始对象与账本、753个逐CIK bundle、聚合manifest、会计恒等式和实体时序QA，并强制权威CIK集合、索引与物理`by_cik`目录完全相等。

发布证书明确记录：`status=source_data_certified`、`factor_stage_required=false`、`experiment_authorized=false`、`formal_eligible=false`。
