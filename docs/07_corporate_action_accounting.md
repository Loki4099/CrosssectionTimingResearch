# 终止性公司行动账本与组合会计 v1

最后更新：2026-08-13

## 1. 为什么复权价格仍不足够

`tr_open/tr_close` 已正确处理普通拆股与现金分红，不能再重复记一笔分红现金流。但它不会自动告诉回测引擎：一只已持有股票在并购后变成了多少现金、多少收购方股票。历史 ticker 的价格序列在退市日终止时，继续持有旧 SID 会造成估值失败；把旧 ticker 直接改成新 ticker 又会把两只不同证券错误地视为同一证券。

因此项目把两类映射严格分开：

- ticker alias：同一证券在数据供应商中的查询别名或一比一改名；
- corporate action：资产所有权发生变化的并购、现金收购或换股，不是 alias。

## 2. 冻结账本字段

账本列固定为：

```text
action_id, action_type, legal_effective_date, apply_session, apply_phase,
source_sid, target_sid, cash_per_source_share, currency,
target_shares_per_source_share, fractional_treatment, evidence_url, notes
```

v1 只接受三类终止性事件：`cash_merger`、`stock_merger`、`cash_and_stock_merger`；`apply_phase` 只允许 `pre_open`。每个事件必须提供可追溯证据，日期、现金和换股比例不得靠价格序列反推。账本会拒绝重复 action ID、同一 source SID 的多个终止事件、负数对价、缺失目标 SID、缺失现金币种、法律生效日前应用，以及落在研究日历范围内但不是交易会话的应用日。

一个项目级账本可以包含样本期外事件；只有落在本次日历起止区间内的 `apply_session` 才要求属于本次交易日历。

## 3. 复权持仓坐标的转换公式

引擎持有的是总回报复权单位，而不是原始股数。设事件前 source 的复权单位为 `u_s`，在法律生效日或此前、且严格早于应用日开盘的最后有效收盘上：

```text
a_s = tr_close_s / raw_close_s
q_s = u_s * a_s
```

其中 `q_s` 是实际股数等价。现金与目标实际股数为：

```text
cash_received = q_s * cash_per_source_share
q_t = q_s * target_shares_per_source_share
```

目标证券在应用日开盘的复权因子与新复权单位为：

```text
a_t = tr_open_t / raw_open_t
u_t = q_t / a_t
```

随后删除 source 持仓，将 `u_t` 加到已有 target 持仓，并把现金对价加入现金余额。若 target 已经持有，单位直接合并。source 在应用日可以完全没有价格行；目标有换股对价时，应用日 `raw_open/tr_open` 缺失必须硬失败，禁止用未来价格或 source 最后价格代替。

为防止开盘前事件读取当日收盘，source 因子候选行还必须满足 `price_date < apply_session`。当前连续 NAV 研究保留精确的分数股等价，不模拟账户层面的整数股取整；`fractional_treatment` 仍保留真实条款用于审计。

## 4. 事件与调仓顺序

每个交易会话严格按以下顺序处理：

1. 开盘前应用公司行动；
2. 若当天是调仓执行日，用转换后的证券和现金计算调仓前 NAV；
3. 执行 Top-K 等权调仓并只对策略交易计换手和成本；
4. 用当日复权收盘估值。

并购造成的强制转换本身记为 `forced_l1_turnover_charged=0`、`forced_cost_amount=0`。如果同日策略选择卖出换得的目标股票，该卖出仍是正常策略交易，必须进入换手和成本。每个场景导出 `corporate_action_events.csv`，记录 source/target 复权因子、实际股数等价、现金、目标复权单位和应用状态。

冻结数据 manifest 可声明：

```json
{
  "corporate_actions": {
    "provided": true,
    "curated_table": "corporate_actions",
    "source": "issuer/SEC evidence ledger v1"
  }
}
```

声明后，baseline runner 会从同一冻结数据版本读取并验证该表；manifest 未声明时使用空账本。所有有换股对价的 target SID 都必须纳入价格抓取范围，即使目标在事件日不是指数成分。

## 5. ESRX → CI 首条事件

当前原型账本位于 `input/prototype_2018_2026/corporate_action_ledger.csv`。首条事件不是 ticker alias：

- source：`yf_ticker::ESRX`
- target：`yf_ticker::CI`
- 法律生效日：2018-12-20
- 开盘前应用会话：2018-12-21
- 每股 ESRX：USD 48.75 现金 + 0.2434 股 CI
- 碎股条款：cash in lieu
- 证据：[Cigna closing Form 8-K](https://www.sec.gov/Archives/edgar/data/1532063/000114036118045477/form8k.htm)

该记录让 2018-12-21 起的既有 ESRX 持仓转换为现金与 CI，而不是要求已终止的 ESRX 序列在之后继续提供虚构估值。

