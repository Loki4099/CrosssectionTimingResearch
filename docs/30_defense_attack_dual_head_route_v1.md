# Defense–Attack 双 Head 条件路线 v1

状态：**路线架构已确定；各后续 Round 仍须独立机器预注册。**

本文件只冻结研究阶段、信息流和 hard-stop 关系，不授权读取 2022–2026 lockbox，也不授权运行模型、最终状态机或动量迁移。Round 6 的具体开发计划见 [Round 6 Attack4 单因子计划](./31_round6_attack4_single_factor_program_v1.md)。

## 1. 已冻结的研究选择

1. **Risk head** 继续以 Round 5 的连续季度级不利路径目标为核心，即 Y5 = max(raw_MAE13 - 5%, 0)。
2. **Attack head** 以未来四周 SPY 相对现金连续超额收益 A4 为唯一 primary；正收益二分类和四周最差路径只作 guardrail。
3. 最终政策为 **risk veto**：risk 报警时 attack 不得覆盖防御决定。
4. 最终投资路线只研究 long-only；不研究 WML、short、leverage、反转或防御资产搜索。
5. 不按 Top-k 晋级因子、模型或 pair。所有通过对应 role-gate 的正式 process 均保留。
6. R7 不对合格 process 设置数量上限；R8 不使用 committee、bagging、stacking 或模型平均。

## 2. 阶段边界

| Round | 唯一问题 | 允许内容 | 明确禁止 |
|---|---|---|---|
| R6 | 单个预定向因子是否具有稳定的 Attack4 信息和经济/条件角色？ | 20 个单因子、A4 primary、binary/worst-path guardrails、固定 attack 经济测量尺、RSP 低风险条件统计、三条预注册 role route | 模型、模型调参、Brier、最终状态机、lockbox、mom255 |
| R7 | 哪些多因子 process 分别能胜任 risk head 或 attack head？ | 独立 target、固定 feature bundle、有限模型家族、严格 nested tuning、role-specific qualification | 跨 head 平均、策略 pair 选择、lockbox、mom255 |
| R8 | 哪些合格 risk × attack pair 在 risk-veto 状态机中具有增量？ | 所有合格 pair、严格 nested selection/validation、统一 100%/50% SPY/cash 测量 | committee、bagging、挑一个 outer winner、mom255 |
| R9 | 已冻结状态能否改善 mom_255_0 long-only？ | Top20 monthly primary；Top10/20/50 × weekly/monthly 六格稳健性 | 其他动量定义、long-short、根据六格结果改状态或选股规则 |

每轮只允许回答本轮问题。上游通过不自动授权下游；下游必须引用上游不可变 manifest，并在读取本轮 outcome 前另行冻结设计、registry、fold、门槛和输出。

## 3. R7：双 Head process 治理

### 3.1 Process 是资格单位

一个正式 process 定义为：

~~~text
task × fixed_feature_bundle × model_family × inner_tuning_rule
~~~

单个超参数点、随机种子或树数不是独立 process。模型家族、feature bundle、调参预算、随机种子和依赖版本必须在 R7 machine registry 中先冻结。

### 3.2 不限数，但必须过 role-gate

- Risk process 只依据 Y5 的 nested OOS 预测能力、严重下行捕获、稳定性与既有 RSP anchor 比较取得资格。
- Attack process 只依据 A4 continuous primary 取得资格；binary 和 worst-path 只能否决。
- 同一模型家族可以有多个合格 process；所有合格 process 均进入 R8，不设每 head 两个、三个或其他数量上限。
- 不允许根据全部 development outer 结果重新选择 feature、改超参数网格或提升未过门 process。
- R7 输出逐 process 的 immutable cross-fitted prediction ledger；不产生 head committee 或最终交易状态。

## 4. R8：全部 pair 的严格 nested 状态机

### 4.1 不做 committee

若 R7 有 N 个合格 risk process 和 M 个合格 attack process，R8 的预登记候选集合就是全部 N × M 个 pair。每个 pair 保持自己的两个预测序列和独立身份，不平均、不投票、不 bagging。

组合数量通过以下方式治理，而不是事后限额：

1. process 粒度固定为模型流程，不把每个超参数/seed 扩成候选；
2. R7 role-gate 先于 pair 形成；
3. pair 状态机公式、仓位、阈值和成本全体一致；
4. pair family 在运行前完整登记，并对全部 pair 做预定多重检验；
5. 不报告“最佳 outer pair”作为冠军；所有通过 pair-gate 的 pair 全部保留各自资格身份，但绝不合并预测或组合仓位；
6. 同角色且 OOS 预测相关性绝对值不低于0.95的 process 保留各自 ledger 身份，但在 multiplicity 中视为一个等价簇；簇代表按“更简单、参数更少、固定字典序”机械确定，禁止按收益选择。

### 4.2 严格 nesting

最外层 policy fold 的 test outcome 只用于验证。对每个 outer fold：

1. 仅在 outer-train 内完成 R7 的变换、调参和 process role qualification；
2. 仅由 outer-train 内合格 process 形成预登记 pair；
3. 在 outer-train 的 state-selection folds 内应用固定 pair-gate；
4. 冻结该 fold 可评估 pair 后，才生成 outer-test 状态和 NAV；
5. outer-test 不得反向改变模型、pair、阈值或状态转移。

最终 development 证据必须来自拼接后的严格 OOS pair 路径。若某 process 在某 outer-train 无法合法拟合，该 fold 的相关 pair 为 invalid，不允许现场替补。

### 4.3 Risk-veto 语义

R8 的首版状态机只允许 NORMAL 与 DEFENSE：

- NORMAL 的风险资产权重为 100%；
- DEFENSE 的风险资产权重为 50%；
- risk process 报警时，下一执行开盘进入或保持 DEFENSE；
- attack process 绝不能在 risk 仍报警时恢复满仓；
- 仅当 risk 已解除且 attack 确认时，DEFENSE 才能回到 NORMAL；
- NORMAL 且 risk 未报警时保持 NORMAL，attack 不用于杠杆或额外加仓；
- risk 缺失时不得增加风险；attack 缺失时不得退出 DEFENSE；
- 最少防守一个 scheduled week。

各 process 的风险阈值、attack 确认阈值和 target 单位必须在 R8 设计中一次冻结；不得由 pair 自行搜索。

## 5. R9：mom_255_0 long-only

R9 只能接收 R8 已冻结且通过 development pair-gate 的状态序列。R9 是最终机械锁箱之前的 development transfer，不得读取锁箱 outcome；所有通过 R8 的 pair 均可按同一冻结规则接受转移检验，不设 top-k。

### 5.1 唯一 primary

- 选股信号：mom_255_0；
- 组合：Top20、monthly、long-only、等权；
- 状态标量：1.0 或 0.5 乘到全部风险资产目标权重；
- 未投资部分持有冻结 RF/cash；
- 选股、调仓、公司行动和执行会计保持裸动量基线不变。

### 5.2 六格稳健性

固定 robustness panel 为：

~~~text
Top10 / Top20 / Top50
×
weekly / monthly
~~~

Top20 monthly 是唯一 primary；其余五格不能成为替补冠军。六格用于判断同一状态是否跨宽度和频率稳定，不允许根据结果删除格子、改变 K 或频率。

R9 只允许 long-only。mom_255_21、mom_12_1、WML、short、leverage、波动率缩放和其他防御资产全部排除。

## 6. 端到端机械 lockbox 与 hard stop

Round 6 的以下授权一律为 false：

~~~text
models_authorized = false
lockbox_authorized = false
final_state_machine_authorized = false
mom255_transfer_authorized = false
~~~

只有 R7–R9 分别完成、全部端到端候选身份与 multiplicity 规则冻结、2022–2026 outcome-free prediction/state/portfolio-target bundle 哈希完成后，新的独立设计才可请求一次 lockbox 授权。锁箱候选不要求只有一个：所有预先合格的端到端系统可以同时冻结并接受 family-wise 校正后的机械检验；若看过锁箱后再从中选择，必须标记为 `mechanical_holdout_assisted_selection`，不得称独立确认。

任一阶段发生以下情况即 hard stop：

- target、available-at、共同样本或父 manifest 不闭合；
- 未产生该角色的合格 process；
- nested firewall 被破坏；
- 状态机未超过其预定 risk-only 与 matched-exposure 控制；
- 端到端 development gate 或 lockbox family gate 失败/不确定。

Hard stop 后禁止 runner-up、改阈值、改窗口、改仓位、增加模型或查看 pair 后救场。R9 与后续机械 lockbox 因相关时期已被研究过程观察，只能称 development transfer / mechanical evidence；部署确认仍需冻结后的 prospective forward。
