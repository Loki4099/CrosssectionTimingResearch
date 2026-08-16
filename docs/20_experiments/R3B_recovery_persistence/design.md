# R3B_RECOVERY_PERSISTENCE：恢复持续性确认——冻结设计

状态：**设计与机器锁已冻结；只授权 development。** 主计划见 [R3B v1](../../25_round3b_recovery_persistence_program_v1.md)。

## 1. 固定范围

本批只训练 `sustainable_attack_4w` 的单一 Ridge Logistic，并把其 outer-OOS 概率用于 R3A 价格恢复出口的确认。模型、特征、阈值、状态机、防守权重和成本均无候选网格。

## 2. 输入锚

- R2A manifest/tree：`071055016268d83f60a03b70be498d85da07897d290b049e4ed7524d1b9e674c` / `6985176ea1088d70c0191d6e24527dc7117e66ce81a1c0ece7ad7f539ed061ce`。
- R2B manifest/tree：`831cf4c4c373a762d7726833fcaebf80db0214c091a640d94955afa283058559` / `3afe3a9a2ebea6ec852eec791e95420fde8ce0c91c1278f004275e9afffafed6`。
- R3A manifest/tree：`6d399a8dc06c7718c5a5f2ae5391b5f62da588259a26412b2e2d582083abb332` / `259fa664b83365c7112fa3fda7bd369347cd16ba4c55f8779889c50c6f887a68`。
- Folds SHA：`e0a18efcd533bd1e836cde4a8e9e9bc3dd0c343eb690b5a7ccc384093bf7c53c`。

## 3. 强制测试

1. 四周标签严格从 execution open 到第四周 execution open，RF 区间为 `[e_t,e_(t+4))`；
2. 目标成熟、5-signal 边界和年度冻结无泄漏；
3. winsor/imputation/standardization 只拟合训练窗；
4. 修改未来或锁箱价格不改变 development feature、prediction、state 和 gate；
5. 相同 R3A 输入得到逐位相同 price-only state/NAV；
6. `p==base_rate` 不退出，模型 invalid 时继续防守；
7. next-open、RF、成本、控制权重和 NAV 恒等式；
8. 文件 hashes、不可变重跑、无 lockbox materialization。

## 4. 运行授权

项目 clean commit 上的 program/design/config/lock hashes 和全套测试全部通过后，才可运行 `r3b-recovery-persistence-development-v1`。本设计不授权 lockbox。
