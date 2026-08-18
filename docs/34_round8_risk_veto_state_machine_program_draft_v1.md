# Round 8：RSP-only risk-veto状态机冻结计划 v1

状态：**已由用户授权冻结并执行development；文件名保留draft仅为历史路径兼容。**

## 1. 父结论与研究问题

Round 7 的27个risk processes与正式AX02 A4 head均为0条合格；raw RSP sentinel仍是最强风险排序。Round 8不改写该结论，也不把AX02称为已验证模型。唯一问题是：同一raw RSP信息在“风险进入”与三种冻结退出语义下，能否形成有经济价值的risk-veto状态程序。

## 2. 三条冻结程序

| policy | risk entry | risk解除后的exit | 身份 |
|---|---|---|---|
| P00_RSP_Y5_CLEAR | raw RSP严格高于outer-train q75 | 最少防守一周后立即NORMAL | Y5-only risk基准 |
| P01_RSP_RAW_RECOVERY | 同P00 | raw `-RSP`严格高于outer-train q75 | 同因子双阈值control |
| P02_RSP_A4_ISOTONIC | 同P00 | R7严格OOF isotonic `E[A4|-RSP]>0` | 双标签探索性候选；parent未合格 |

A4 head单独没有risk-entry语义，不注册为独立风控策略。三条程序只读取R7B raw-RSP outer-OOS预测与R7C AX01/AX02 outer-OOS预测；不重拟合、不搜索阈值、不读取全样本结果选pair。

## 3. 状态规则

状态仅有 `NORMAL=1.0 SPY` 与 `DEFENSE=0.5 SPY`。每周close产生信号、下一XNYS open执行：

- risk high时无条件进入/保持DEFENSE，attack不能推翻；
- risk解除后，P00立即恢复，P01须raw recovery high，P02须预期A4严格大于0；
- 每次进入DEFENSE至少持有一个完整scheduled week；
- 缺risk不得增加风险，缺attack不得退出DEFENSE；
- 2014–2021 outer年度连续carry state，不按年份重置；
- attack在NORMAL时不加杠杆。

## 4. 经济评价

共同样本为R7的404个outer-OOS周；NAV从首个execution open运行至2021-12-31。每条程序运行0/5/10/20bp，SPY/cash目标仅1.0/0.5；matched-static使用该程序实际daily target的平均暴露，另报always-SPY。

共同经济门：10bp和20bp相对matched-static终值均>0、10bp正主动年度比例>=60%、10bp MDD不差于matched-static、六事件逐一删除主动收益后均>0。成本不得改变状态。

P01/P02相对P00的唯一正式增量统计为共同周主动log-return差均值，13周moving-block 5,000 draws，两个p值统一Holm FWER 0.05；同时要求MDD不恶化、premature re-entry与MAE10风险暴露不增加。P02即使经济门通过也只能称 `exploratory_dual_label_mechanism_positive`，不能抹去Round 7 head资格失败。所有通过路径保留，不设Top-k。

## 5. 边界

Round 8只授权状态生成、SPY/cash development replay与资格审计。bagging、stacking、2022–2026锁箱、mom255迁移、仓位/阈值搜索均关闭；R8C后硬停，等待用户决定Round 9。
