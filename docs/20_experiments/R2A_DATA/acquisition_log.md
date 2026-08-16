# R2A_DATA acquisition log

本页只记录数据获取与 QA，不包含 T1/T2/T3、特征表现、模型分数、策略回测或经济结论。

## 2026-08-16：免费数据源范围决策

- 用户确认本轮只使用免费或现有免费层数据做本地、不分发的研究；Tiingo、Cboe 与 Kenneth French 的 L 线许可门据此记为 `approved_for_local_research`。
- Norgate 暂不考虑，S/PIT 增量线继续保持 `blocked_external_dependency`，不购买、不抓取、不用现代成分股回填历史，也不以不可靠的免费历史成分数据替代。
- R2B/R2C 先仅使用 L 线 core。F4/F5 与条件 `PIT01` 本轮不开放，不用其他特征补位；这不改变 17 个 long-core trial-arm 预算。
- 后续 `R2D_MOM255_TRANSFER` 仍可在唯一 champion 通过后使用既有、不可变的 2018–2026 PIT v3 做开发期迁移；不得把它扩写成 1993+，也不得称为独立外部确认。
- 首次 staging 保持不可变。许可确认后的 candidate 必须从同一组已哈希 raw bytes 新建目录并完整重建，禁止原地改 manifest 或补写 `FROZEN.json`。

## 2026-08-16：L 线首次 staging

- snapshot：`r2a-long-preflight-20260816-v1`
- 本地路径：`C:\Users\17866\QuantWork\MomentumRversionMethod-runtime\data\round2\staging\R2A_DATA\r2a-long-preflight-20260816-v1`
- 状态：`staging_review`
- manifest SHA256：`ffb3e3f09c7dc440ca39886ac19f1f032e987fffa146e091354aa8d966d4ccf6`
- 15 文件 tree SHA256：`905f8f979fc8fb75b1194ada37957d31318b9cb2235a0f93bba19156e0d21a56`
- 总字节数：5,989,890
- `FROZEN.json`：不存在；许可门仍为 `pending_human_confirmation`
- 从上述 raw bytes 独立重建两次，四张 curated 表的 canonical-content SHA256 均逐项等于 manifest：
  - `market_daily`：`1d2b4393458204cc054908f8cc7bd8b7cb0ed75d0a91a3d8bb7167700a0cb744`
  - `risk_free_daily`：`4c737c8617159d7f335919d0e5fbafc70568153e45cf807aff85a419f245b695`
  - `vix_daily`：`38036dc0441e389e50b66f2444bcabe6c90e8dee3e2be3c0864e7cce2ae3e06e`
  - `decision_calendar`：`a7a67461ae3dad8bfd361daf1571cd7f8c3697b4f8cf62dba53abf7ade379cc6`

### 覆盖结果

| 表 | 行数 | XNYS required 缺口 | 结论 |
|---|---:|---:|---|
| Tiingo SPY `market_daily` | 8,411 | 0 | required gate 通过 |
| French `risk_free_daily` | 8,411 | 0 | required gate 通过；重叠 v3 RF 逐位一致 |
| `decision_calendar` | 1,744 | — | 1993-01-29 signal 起；实际节假日映射 |
| Cboe `vix_daily` | 8,409 | 2 | optional F3 失败，不阻断 core |

Cboe 当前 CSV 原缺 1997-01-31、1997-11-26、1999-12-31。用同一 Cboe 官方 1990–2003 XLS 旧档交叉核验后只恢复 1999-12-31；两份官方源在 2,750 个共同日的 close 最大差为 0。仍缺的 1997-01-31 是周频信号日，因此 F3 固定记为 `invalid_data/not_available`，不得插值、前填或换第三方源。

### 与冻结 v3 重叠期

2018-01-02 至 2026-06-30 共 2,134 日：

- RF 最大绝对差：0；
- SPY adjusted open 相对差最大约 0.07076%；
- SPY adjusted close 相对差最大约 0.07076%；
- close-to-close 日收益差最大约 2.5003bp；超过 1bp 的日期 2 个。

旧 v3 与本次不是同一下载 snapshot；本次保留逐日 reconciliation，不把差异静默覆盖或拼接。R2A 若晋级将使用完整的新 Tiingo 1993–2026 单源快照；第一轮 v3 及所有 bundle 保持不可变。

### 未开放事项

- 未创建 completed/FROZEN 数据版本；
- 未计算 target、特征、模型、预测、NAV 或回测；
- S/PIT 线因 `norgatedata`、本地数据库与订阅尚未就绪，保持 `blocked_external_dependency`；
- 下一门是：确认源许可仅供本地非分发研究，然后对同一 source snapshot 做第二次 clean build，核对 canonical-content hashes 后才允许冻结 L 线 candidate。
