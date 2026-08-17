# R4A 免费因子数据门禁报告

R4A 已完成并通过双构建、父锚、不可变与 outcome 防火墙验收。正式 bundle 为 `r4a-free-factor-data-20260817-v1`，manifest SHA256 `1b0b27f689bb3966a34ca94076467be7dad209afa8910a516827b0419514dd7f`，14-file tree SHA256 `12ad10c68ae2d708ce6999cf599d5155429532efc947da84ed99f08afb5cd096`。

| 结果 | 数量 |
|---|---:|
| 预登记 arm | 20 |
| reference eligible | 17 |
| invalid_data | 3 |
| weekly feature rows | 30,180 |
| 17-arm 共同有效周 | 961 |

失败分支没有补位：`HY_OAS_LEVEL` 与 `HY_OAS_CHANGE21` 因 FRED 免费公开序列现只保留开发期之后的历史而失败；`NFCI` 因无法取得合格的 release/vintage-as-of 序列而失败。Treasury、Cboe VIX、父快照 SPY volume 与 Tiingo RSP 均通过冻结规则。VIX 有 1 个周信号缺口，低于 2%/4周门槛且未填补。

旧十因子与 R2B 重叠值逐位相同；两次独立构建的四张 canonical 表哈希相同。同 run-id 重跑即时拒绝。最大 signal 为 2021-12-23；target、策略、事件 outcome 与 2022+ lockbox 均未在 R4A 物化。
