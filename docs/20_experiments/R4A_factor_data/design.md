# R4A_FREE_FACTOR_DATA：免费因子数据与 availability 门禁

- 状态：`plan_locked_authorized_not_run`
- program：`defense_factor_audit_round4_v1`
- 授权：仅数据获取、特征输入构造与 QA；禁止 target、策略和事件 outcome

## 1. 目标

为 Round 4 已登记的 20 个单因子 arm 建立免费、可复现、带 `available_at` 的开发期输入。R4A 只回答“能否在冻结规则下可靠构造”，不回答“是否能预测或赚钱”。

## 2. 只读父锚

- R2A manifest：`071055016268d83f60a03b70be498d85da07897d290b049e4ed7524d1b9e674c`
- R2A tree：`6985176ea1088d70c0191d6e24527dc7117e66ce81a1c0ece7ad7f539ed061ce`
- R2 folds：`config/experiments/round2/folds.json`
- folds SHA256：`e0a18efcd533bd1e836cde4a8e9e9bc3dd0c343eb690b5a7ccc384093bf7c53c`

R2/R3 的数据、设计、锁和结果不可修改。R4A 建立新 dataset/version，不覆盖父 bundle。

## 3. 允许的数据线

### 3.1 既有 market core

只读复用 R2A 的 SPY total-return OHLC、RF 与 XNYS calendar，重建旧 10 因子的输入。普通估值继续使用 total-return-adjusted series；不得混用 raw 与 adjusted return。

### 3.2 新免费数据

| 分支 | 首选免费来源 | 用途 | 关键限制 |
|---|---|---|---|
| Cboe VIX | Cboe 官方历史文件 | VIX level、VIX–RV gap proxy | 不填补；保留缺失周 |
| SPY raw volume | R4A 冻结的单一 provider snapshot | 两个量价 arm | raw close、raw volume、split/单位 QA |
| HY OAS | FRED `BAMLH0A0HYM2` | level、21-session change | signal 使用至少 lag 1 session 的 as-of 值 |
| Treasury | FRED `DGS10`,`DGS3MO`,`DGS2` | 10Y–3M、10Y–2Y | 同一发布日期与 staleness 规则 |
| NFCI | ALFRED/FRED vintage archive | financial conditions | 必须是 release/vintage-as-of；无 vintage 则 invalid |
| RSP | R4A 冻结的单一 provider snapshot | RSP/SPY participation proxy | 只称 investable proxy；TR close 对齐 |

暂不使用 Norgate，不使用当前成分回填历史 breadth，不引入付费源。source priority 只能在 target/outcome 未物化前按数据 QA 固定；一旦 resolved，不得因结果更换。

## 4. 冻结公式输入

SPY dollar volume 定义为 `DV_t = raw_close_t * raw_volume_t`。两个量价输入为：

```text
down_move_dv_share21 =
  sum(DV * abs(TR_logret) * 1[TR_logret < 0], 21)
  / sum(DV * abs(TR_logret), 21)

volume_shock21_252 = log(mean(DV, 21) / median(DV, 252))
```

分母非正、非有限或窗口不完整时缺失，不做填补。VIX 先从百分数除以 100；variance-gap proxy 为 `(VIX/100)^2 - RV21^2`。RSP arm 使用 63-session `log(RSP_TR/SPY_TR)` 变化的负值。

FRED daily 分支在 signal 日只能读取前一 XNYS session 已发布的值，最多 5 sessions staleness。NFCI 只能读取 `available_at <= signal_timestamp` 的 vintage，最多 carry 14 calendar days。

## 5. 缺失与 eligibility

- VIX、SPY volume、RSP 不插值、不前后填；
- 缺 score 的计划周在后续经济 replay 中 carry prior overlay state，paired control 同步；首个有效状态前为 risk-on；
- pre-inception 不计入缺失率，也不得回填；
- 每个 arm 的 eligible span 内，计划周缺失率必须 `<= 2%`，最长连续缺失 `<= 4` 周；
- 正式 reference flag 至少需要 8 个完整 OOS execution years、400 个有效 OOS 周；否则只能 `descriptive_only`；
- 共同交集只有在至少 520 个周信号时才作为统一比较表；
- source、单位、调整、available-at、staleness或历史不变性失败则对应 arm `invalid_data`，不得替换。

这套 VIX 缺失政策是 Round 4 的新独立规则。它不追溯改变 R2A/R2B 对 100% signal coverage 的旧门禁或旧结论。

## 6. QA 硬门禁

每个 source/arm 必须通过：

1. `(asset/date)` 或 `(series/date/vintage)` 唯一；
2. 时间戳、timezone、单位、调整与许可记录明确；
3. raw payload、normalized table、feature-input table 都有 bytes 与 SHA256；
4. 修改 signal 之后的源记录不得改变该 signal 的输入；
5. 同一 snapshot/config 两次独立构建的表哈希相同；
6. missing/staleness/coverage 按周和按年完整报告；
7. 与 R2A 重叠的 SPY/RF/calendar 不变量一致；
8. 旧 10 因子的重叠 feature 值可逐行回归校验；
9. R4 输出截断在 development firewall，不生成 2022+ feature outcome联表；
10. target、策略 NAV、事件 peak/trough 分类在 R4A 文件中均不存在。

任何 required core gate 失败使 R4A `invalid_data/fatal`；可选新分支失败只使其预登记 arm `invalid_data`，不阻断其他分支。

## 7. 产物合同

R4A bundle 至少包含：

- `manifest.json`
- `source_inventory.csv`
- `availability_weekly.parquet`
- `feature_inputs_weekly.parquet`
- `factor_eligibility.csv`
- `common_mask_candidate.csv`
- `qa_summary.csv`
- `config_resolved.toml`

完整表保留在本地 runtime；Git 只发布 manifest、配置、eligibility、QA summary 和采集说明。bundle 必须 immutable，同 run-id 重跑拒绝。

## 8. 授权边界与完成条件

R4A 可以抓取网络免费数据和构造上述输入，但在当前 `PLAN_LOCK` 下不得导入或计算 T1/T2/T3，不得读取 2022–2026 outcome，不得回测 SPY/RF，不得生成 drawdown event atlas。

完成后先只读验收 bundle 和 eligibility，再冻结 R4B/R4C/R4D designs、resolved factor registry、共同周 mask 和 `PREREG_LOCK.json`。该第二道锁生成前，后续批次状态保持 `not_authorized`。
