# R6A Attack4 target：冻结设计

状态：**development target materialization only**

父计划：[Round 6 Attack4 单因子角色审计计划](../../31_round6_attack4_single_factor_program_v1.md)

## 1. 授权范围

本批以 R3B 不可变 `targets_weekly.parquet` 的 A4/B4 和 R2B 不可变 target bundle 的 W4 为父锚，读取冻结的 SPY total-return OHLC、逐日 RF 与 XNYS 决策日历做逐值身份重建，再发布 Round6 target bundle。R3B target 文件 SHA256 固定为 `9295168ba695bc05bce35a7850a260247f0965d873190ee5ad042b9d368a0a67`；R2B target 文件固定为 `5056c6ebef6140bbd5f08f487584016ea1e5abc32462bde41caa230e2514e3e8`。

本批不得读取因子表现，不得生成单因子排名、策略、模型、最终状态、mom255 路径或 2022–2026 outcome。

## 2. 时点

- s_t：该周最后一个 XNYS session 收盘；
- e_t：s_t 后下一 XNYS session 开盘；
- e_(t+4)：第四个后续计划执行开盘；
- 起点隔夜不进入新标签；
- target_available_at 为路径中全部价格、RF 与调整记录真实可读时点的最大值；
- 训练/评价使用 target 时必须满足 target_available_at <= 对应信息截止时点。

若 terminal execution 为 2022-01-03 或以后，A4、B4、W4 及全部派生 outcome 必须为空。

最后合法 signal 固定为 2021-11-26，terminal/target_available_at 为 2021-12-27；更晚 signal 只能保留时点与 withheld 标记，不能保留 outcome。

## 3. Target

### 3.1 A4 primary

~~~text
A4 =
log(SPY_TR_open[e_(t+4)] / SPY_TR_open[e_t])
- sum(log(1 + RF_d), d in [e_t, e_(t+4)))
~~~

### 3.2 B4 guardrail

~~~text
B4 = 1[A4 > 0]
~~~

等于零归 B4=0。

### 3.3 W4 guardrail

对起点0、期间每个 session close 及终点 open 的 SPY 相对现金累计对数财富取最小值：

~~~text
W4 = min(0, all path values, A4)
severe_W4 = 1[W4 <= log(0.95)]
~~~

路径任一必要价格、RF 或 terminal 缺失时，整行 target invalid；不得插值、前填或用 close 替代 open。

## 4. QA

必须验证：

1. week_id、signal、execution、terminal execution 唯一且严格递增；
2. A4 与 terminal path value 逐行一致；
3. W4<=0 且 W4<=A4；
4. B4 只取0/1并与 A4 符号一致；
5. severe_W4 与固定 log(0.95) 阈值一致；
6. 2022+ target 防火墙为零违规；
7. 改变未来记录不得改变过去已成熟 target；
8. 两次独立构建产生相同 bytes/hash。
9. 与 R3B 的 A4/B4、R2B 的 W4 在共同合法行逐值完全一致。

## 5. 输出

至少输出：

~~~text
week_id
signal_timestamp
execution_timestamp
terminal_execution_timestamp
target_available
target_available_at
a4
b4
w4
severe_w4
invalid_reason
~~~

仅报告覆盖、成熟性、分布、年度 attack rate、W4 分布和相邻周依赖；不报告任何因子结果。

## 6. 授权标记

~~~text
models_authorized = false
lockbox_authorized = false
final_state_machine_authorized = false
mom255_transfer_authorized = false
~~~
