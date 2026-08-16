# R3A_ASYMMETRIC_REENTRY：波动率进入、价格恢复退出——冻结设计

状态：**设计与机器锁已冻结；只授权 development。** 主计划见 [Round 3 v1](../../24_round3_asymmetric_defense_reentry_program_v1.md)。

## 1. 单一 treatment

本批只有一条 treatment：SPY RV21 超过严格滞后 756-session q75 时，下周开盘把 SPY 从 100% 降到 50%；已在防守状态时，两个连续收盘严格站上各自因果 SMA21 后，下周开盘恢复 100%。恢复后必须先观察到 `RV21<=q75` 才重新 armed。

不得扫 RV 窗口、分位数、SMA 窗口、确认天数、防守仓位、成本或状态优先级。

## 2. 唯一输入与评价段

- R2A manifest SHA256：`071055016268d83f60a03b70be498d85da07897d290b049e4ed7524d1b9e674c`。
- R2A tree SHA256：`6985176ea1088d70c0191d6e24527dc7117e66ce81a1c0ece7ad7f539ed061ce`。
- 周历/folds SHA256：`e0a18efcd533bd1e836cde4a8e9e9bc3dd0c343eb690b5a7ccc384093bf7c53c`。
- Development 只允许 execution year 2005–2021；signal 最大日期必须早于 `2021-12-31`。
- Development runner 读取 market/RF 后必须先按 development 日界裁剪，再计算任何状态或结果；manifest 必须写 `lockbox_materialized=false`。

## 3. 输出合同

不可变 bundle 至少包含：

- `weekly_states.parquet`：周历、RV21、q75、SMA21 当前/前日、entry/recovery/rearm、pre/post state、目标权重、退出原因；
- `nav.parquet`：三条路径 × 0/5/10/20bp 的逐日 NAV、收益、SPY/现金权重、成本；
- `summary.csv`：CAGR、Sharpe excess-RF、MDD、vol、beta、L1、成本、平均暴露；
- `controls.csv`：同平均暴露/同波动权重与相对终值；
- `mechanism.csv`：benefit、missed upside、net timing、capture、episode 与危机窗口；
- `gate.json`：H1–H4 每个原子条件与总判定；
- `config_resolved.toml` 与 `manifest.json`。

所有日期/strategy/cost 键唯一，NAV 正且有限；daily return、现金、成本、暴露和 NAV 逐日闭合。bundle 已存在即在读数据前拒绝覆盖。

## 4. 因果与单元测试

必须覆盖：

1. RV21 使用当前收盘但 q75 严格只用前 756 个 RV；等号不进入；
2. SMA21 只使用各自当日及以前 21 个收盘；必须连续两个 session 严格站上；
3. 状态优先级、最短一周防守、unarmed hysteresis、re-arm 不交易；
4. 修改未来价格不改变过去状态，修改当前收盘只能影响当前及以后；
5. 锁箱价格扰动不改变 development bundle 文件哈希；
6. 周五/周一假日按 XNYS 实际 session；
7. next-open 会计、RF、成本、静态控制和机制恒等式；
8. 同 run-id 的不可变拒绝和 manifest 全文件 bytes/SHA 验证。

## 5. 运行授权

只有项目 clean commit 中的 program、design、config 与 lock hashes 全部匹配，且全套测试通过后，才可运行 development bundle `r3a-asymmetric-reentry-development-v1`。当前文件本身不授权 lockbox。
