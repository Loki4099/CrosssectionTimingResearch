# R6B Attack4 单因子：冻结设计

状态：**development single-factor diagnostics only**

父计划：[Round 6 Attack4 单因子角色审计计划](../../31_round6_attack4_single_factor_program_v1.md)

## 1. 固定 arm

本批恰有20个 arm：

- 17个 level，逐位复用 Round5 factor registry；
- 三个固定 Δ4：SMA_GAP、RV_RATIO、RSP_SPY63。

令 D_t 为冻结 defense score：

~~~text
level attack score = -D_t
delta attack score = D_(t-4 scheduled observations) - D_t
~~~

方向、窗口和变换不得根据结果修改。缺失精确 t-4 时 delta 缺失，不得寻找替代观察。

## 2. 样本

- 同时保存 arm-native sample 与20-arm common-valid sample；
- primary multiplicity 与可比表使用 common-valid sample；
- native sample 只用于方向一致性与数据覆盖诊断；
- 所有 target 必须来自 R6A 不可变 bundle；
- 2022+ target 或 outcome 读取为 fatal leakage。

## 3. Primary

唯一 primary 统计量：

~~~text
Spearman(attack_score, A4)
~~~

报告 raw rho、causal top-quartile 与 rest 的 A4 均值/中位数差、正向收益 capture、B4 rate差与 lift、AUC、W4 median差、severe_W4 rate差及年度方向。正向收益 capture 为 top组 `sum(max(A4,0))` 占共同样本同口径总和；B4 lift 为 top组 B4 rate 除以共同样本 base rate。

本批不得训练任何模型，包括但不限于 linear regression、logistic、Ridge、GAM、RF、LightGBM 或 XGBoost；不得计算 Brier、校准曲线或模型 loss。

## 4. 推断

- primary moving block：4 scheduled weeks；
- bootstrap draws：2,000；
- 单侧 alternative：rho>0；
- 20项 primary p-value 统一 BH-FDR，q<=0.10；
- 禁止 iid t-test。

另运行8 scheduled weeks moving-block veto：

- 不进入 primary p/q；
- 不能晋级4周未通过 arm；
- 已通过4周门但8周 Spearman 点估计<0时，标记 robust-direct veto；8周区间只报告。

## 5. Guardrails

Binary：

- AUC>0.5；
- causal top-quartile B4 rate>rest。

Worst path：

- causal top-quartile median W4>=rest；
- causal top-quartile severe_W4 rate<=rest。

B4/W4 不能独立选择 arm，也不能救回 A4 primary 失败。

## 6. 输出与边界

输出至少包括 arm registry、availability、native/common metrics、4周 block summary、8周 veto summary、BH table、年度表和 guardrail table。

本批不生成经济路径；R6C 只能读取本批冻结 score/threshold inputs，不得反向改变本批统计。

~~~text
models_authorized = false
lockbox_authorized = false
final_state_machine_authorized = false
mom255_transfer_authorized = false
~~~
