# Round 6：Attack4 连续单因子角色审计计划 v1

状态：**preregistered_development_authorized / development only**

计划编号：**attack4_single_factor_round6_v1**

证据等级：development / free-data / formal_eligible=false

本轮只审计单因子 Attack4 信息与固定经济角色代理。无论结果如何，本轮均不授权模型、2022–2026 lockbox、最终双 head 状态机或 mom_255_0 迁移。

## 1. 研究问题

Round 5 已回答哪些 defense score 能排序季度级不利路径。Round 6 改问：

> 一个预定向的单因子 attack score，能否稳定排序从下一可成交开盘开始的未来四周 SPY 相对现金连续超额收益，并且不以更差的中途路径换取期末反弹？

A4 continuous 是唯一 primary。正收益 binary 与四周 worst path 只作 guardrail。本轮不训练任何统计学习模型，不进行概率拟合、Brier 评价、超参数搜索或特征组合。

## 2. 时间、执行与防火墙

- signal：每周最后一个 XNYS session 收盘；
- execution start：下一 XNYS session 开盘 e_t；
- target end：第四个后续计划执行开盘 e_(t+4)；
- development outcome 最晚为 2021-12-31；
- 最后一个合法 target signal 为 2021-11-26，target_available_at 为 2021-12-27；
- 固定经济代理最后 signal 为 2021-12-23，NAV 截止 2021-12-31；
- signal 2021-12-31 / execution 2022-01-03 起属于机械 lockbox；
- 任何 target endpoint 进入 2022-01-03 及以后时，该行 target 必须为空；
- lockbox target、预测、状态、NAV 与 metric 均不得生成。

所有价格使用冻结 SPY total-return 序列；现金使用冻结逐日 RF。signal close 到 execution open 的隔夜收益属于旧持仓，不进入 A4。

## 3. 三个 target 视图

### 3.1 唯一 primary：A4 continuous

~~~text
A4_t =
log(SPY_TR_open[e_(t+4)] / SPY_TR_open[e_t])
- sum(log(1 + RF_d), d in [e_t, e_(t+4)))
~~~

A4 越高，未来四周越支持 attack/risk-on。成本不进入 target。

### 3.2 Binary guardrail

~~~text
B4_t = 1[A4_t > 0]
~~~

A4 等于 0 时 B4=0。B4 只用于 attack-rate、AUC 和 top-vs-rest 方向诊断；禁止训练 logistic、概率校准或计算 Brier。

### 3.3 Worst-path guardrail

从 e_t 开盘财富比 1 开始，对区间内每个 session close 及 e_(t+4) open 计算 SPY 相对现金累计对数财富：

~~~text
W4_t = min(0, all relative-cash path values, A4_t)
~~~

W4 恒不大于 0；严重路径固定为 W4 <= log(0.95)。W4 只能否决，不得用于选择方向、扫描阈值或产生独立 winner。

## 4. 冻结的 20 个单因子

### 4.1 17 个 level

逐位复用 config/experiments/round5/factor_registry.csv：

1. RV21
2. RV126
3. RV_RATIO
4. DOWNSIDE_VAR63
5. RET21
6. RET126
7. SMA_GAP
8. DRAWDOWN252
9. SKEW63
10. KURT126
11. VIX_LEVEL
12. VIX_RV_GAP
13. DOWN_VOLUME21
14. VOLUME_SHOCK
15. YC_10Y3M
16. YC_10Y2Y
17. RSP_SPY63

令 D_t 为 registry 中“越高越防御”的冻结 defense score。每个 level 的唯一 attack score 为：

~~~text
A_level,t = -D_t
~~~

不得根据结果翻转方向。

### 4.2 三个固定 Δ4

只允许：

1. D4_SMA_GAP
2. D4_RV_RATIO
3. D4_RSP_SPY63

定义：

~~~text
A_delta,t = D_(t-4) - D_t
~~~

t-4 指第四个此前 scheduled weekly observation，不是四个交易日或四个自然周。正值表示 defense score 在四个计划观察间下降，预定向为更支持 attack。

若 D_t 或精确 D_(t-4) 缺失，该 delta 缺失；不得回找最近有效周、填补或改变窗口。禁止新增其他 delta、窗口、斜率、百分比变化、加速度或替补。

## 5. 批次

1. **R6A_ATTACK4_TARGET**：只物化 A4、B4、W4、成熟性与因果 QA；
2. **R6B_ATTACK4_SINGLE_FACTOR**：20 个 raw attack score 的单因子统计；
3. **R6C_ATTACK4_ROLE_PROXY**：固定 q75 attack 经济测量尺与 RSP 低风险条件统计；
4. **R6D_ATTACK4_ROBUSTNESS**：共同样本、主 4 周 block、8 周 veto、BH-FDR、年度和事件稳健性，并执行 hard stop。

各批按顺序完成；不得根据中间结果修改后续门槛。

## 6. R6B 主统计与 guardrails

### 6.1 Primary

主统计量为：

~~~text
Spearman(attack_score, A4)
~~~

并报告 causal q75 attack-score top 25% 与其余 75% 的 A4 均值差、中位数差、正收益率差及年度方向。

正向收益 capture 固定为：

~~~text
sum(max(A4,0) for top-25%) / sum(max(A4,0) for all common weeks)
~~~

Binary lift 固定为 top-25% 的 B4 rate 除以共同样本 B4 base rate；分母为零时记为不可评价，不得另换口径。

### 6.2 主推断 block

- 主 moving-block 长度：4 个 scheduled weeks；
- bootstrap draws：2,000；
- 单侧方向：greater than zero；
- 20 个 arm 的 primary Spearman p-value 统一做 BH-FDR；
- primary robust 门：4周 block 单侧 95% lower bound >0 且 q<=0.10。

禁止使用 iid t-test 或把 8 周 block 作为 primary。

### 6.3 8 周只作 veto

同一统计量另做 8 周 moving-block bootstrap。8 周结果：

- 不能使任何未通过 4 周 primary 的 arm 晋级；
- 不进入 BH primary family；
- 只能否决已经通过 4 周门的 arm；
- 若 8 周 Spearman 点估计 <0，则该 arm 不得取得 robust-direct role；8周区间只报告，不作为第二套晋级门。

### 6.4 Guardrails

Binary guardrail 必须满足：

- AUC(attack_score, B4)>0.5；
- top-25% attack rate 高于 rest。

Worst-path guardrail 必须满足：

- top-25% 的 median W4 不低于 rest；
- top-25% 的严重路径率不高于 rest。

Binary/W4 不产生独立 p-hacking family，也不能救回 primary 失败。

## 7. R6C 固定经济角色代理

每个 arm 只允许以下测量尺：

~~~text
attack_score > causal historical q75  -> 100% SPY
otherwise                            ->  50% SPY
~~~

- q75 严格使用当前 execution year 以前的有效历史；
- 至少 260 个历史有效周；
- 严格大于；等号归 50%；
- 缺失 score 时沿用上一目标且不产生 overlay trade；
- 每周下一执行开盘恢复目标；
- 主成本 10bp/每美元 SPY 实际交易额；
- 压力成本 0/5/20bp；
- 主控制为同日历、同成本、同平均 SPY 暴露的静态 SPY/cash replay。

该代理只回答 attack score 是否具有条件持有更多股票的经济方向。它不是最终双 head 状态机，不允许 risk/attack 联合阈值、滞回、确认周数或仓位搜索。

RSP 低风险条件固定为：

~~~text
RSP defense score <= its causal historical q75
~~~

仅在该条件内报告 A4 均值/中位数、B4 rate、W4 median/严重路径率及 attack top-vs-rest 差。不得据此生成联合交易状态。

## 8. 三条 Role route 与无 Top-k

Round 6 不用一条全指标交集重新删除“统计弱、但具有条件或经济角色”的因子。机器目录预先限定每个 arm 可申请的 route；三条 route 分别给出身份，不互相冒充：

### 8.1 robust_direct_attack

仅 `direct_eligible=true` 的 arm 可申请，必须满足：

1. reference attack 五项均通过：Spearman>0、正向收益 capture>25%、top mean A4>rest、B4 lift>1、至少60%完整年度 Spearman>0；
2. 4周 block 单侧95% lower>0 且20项统一 BH q<=0.10；
3. 正向收益 capture>=35%，top mean A4>0，B4 lift>=1.10；
4. binary 与 W4 guardrails 通过；
5. arm-native/common 同向，逐一删除 major event 后 Spearman>0；
6. 8周 Spearman 点估计不为负。

### 8.2 economic_reference

`context_only=false` 的 arm 可申请。它不宣称边际统计发现，但可作为 R7 的经济参考输入，必须满足：

1. 10bp 与20bp相对 matched-exposure active terminal wealth 均>0；
2. 至少60%完整年度 active contribution>0；
3. 动态 MDD 不差于 matched-exposure static；
4. binary/W4 harm veto 未触发。

### 8.3 conditional_eligible

仅 `conditional_eligible=true` 的 arm 可申请。它只取得条件角色，不得称 direct champion：

1. RSP low-risk 内 attack-high 与其余周的 A4 均值差为正；
2. 每个必要 cell 同时不少于共同周5%且不少于44周；
3. 该差值的4周 block 单侧95% lower>0；
4. 所有注册 conditional arm 的 p 值统一 BH q<=0.10；
5. 同条件 W4 harm veto 未触发。

最终 `model_input_eligible = robust_direct_attack OR economic_reference OR conditional_eligible`。两条 yield-curve arm 固定为 `context_only`，只进入分层报告，永不由 R6 自动晋级模型输入。

不产生 Top1、Top3、Top5 或唯一冠军。所有通过任一获准 route 的 arm 均保留相应身份；未获资格者不能补位，某一 route 的强项也不能改写另一 route 的结论。

## 9. R6D hard stop

若 `model_input_eligible` arm 数为 0：

~~~text
assessment = completed_no_attack_role_candidate
models_authorized = false
lockbox_authorized = false
final_state_machine_authorized = false
mom255_transfer_authorized = false
~~~

若存在至少一个 `model_input_eligible` arm：

~~~text
assessment = completed_attack_role_candidates_development_only
models_authorized = false
lockbox_authorized = false
final_state_machine_authorized = false
mom255_transfer_authorized = false
~~~

也就是说，阳性只允许形成下一份 R7 预注册提案；R6D 完成后无条件停止。禁止在本轮追加模型、改 target、改 block、改 q、改窗口、改方向、提升 runner-up、读取 lockbox 或启动 mom255。

## 10. 不可变输出

各 bundle 至少包含 target/score availability、20-arm registry、native/common metrics、4周/8周 block draws summary、BH table、年度/事件表、固定经济代理、RSP 条件统计、gate、resolved config 与 manifest。

所有 manifest 必须记录父数据/设计哈希、代码 commit、dependency lock、文件 bytes/SHA256，并明确：

~~~text
lockbox_read = false
models_run = false
final_state_generated = false
mom255_read = false
~~~
