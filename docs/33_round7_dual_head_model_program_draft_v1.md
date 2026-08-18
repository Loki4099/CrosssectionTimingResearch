# Round 7：Risk 多因子模型与 RSP Attack head 冻结计划 v1

状态：**用户已确认研究设计；只有本文件及配套registry/folds通过机器哈希锁后才授权执行。文件名保留 `draft` 仅作讨论期provenance，不表示锁后仍可修改。**

本轮承接 [Round 6 决策备忘录](./32_round6_attack4_single_factor_decision_memo.md)。Round 6 的机器结论仍是 `model_input_eligible=0`；本计划不会把它改写为通过。RSP/SPY63 由用户依据跨标签机制证据明确指定为 `cross_target_mechanism_candidate`，RET126、RV126、SMA gap 与 VIX level 只以 `risk_only_exploratory_sensor` 身份进入有限风险模型池。

## 1. 本轮唯一问题

Round 7 只回答两个 head 层问题：

1. 在 Y5 风险任务上，RSP anchor 加入趋势/风险水平传感器后，哪些冻结模型流程能产生稳定的严格时序 OOS 风险分数？
2. 在输入仍只有 RSP 的情况下，用 A4 监督得到的简单单调 attack head，是否比无监督的 RSP 恢复分数提供可用的校准信息？

本轮不运行最终状态机、不比较 risk×attack pair、不生成策略 NAV、不读取2022–2026 outcome，也不运行 `mom_255_0`。

## 2. 两个标签与方向

### 2.1 Risk head

唯一 risk target 为冻结 R5A：

~~~text
Y5 = max(raw_MAE13 - 5%, 0)
~~~

高 risk score 必须表示未来13周可避免不利路径更严重。MAE5/MAE10只作 incidence/tail guardrail，不产生另一个模型冠军。

### 2.2 Attack head

唯一 attack target 为冻结 R6A：

~~~text
A4 = future four-scheduled-week SPY excess log return over cash
~~~

高 attack score 必须表示未来四周更适合持有 SPY。`1[A4>0]`与W4最差路径只作诊断/否决。

两个标签相关但不等价；共同2005–2021周的 Spearman 约为-0.551。风险仍具有优先权，A4 head 永远无权覆盖高风险判断。

## 3. 冻结风险特征语法

所有输入逐值复用 R4A `defense_score`，高值统一表示风险更高。RSP 是每个模型的强制 anchor；趋势簇与波动簇各至多一个，禁止任意子集搜索。

| bundle | 冻结输入 |
|---|---|
| RB00 | RSP_SPY63 |
| RB01 | RSP_SPY63 + RET126 |
| RB02 | RSP_SPY63 + SMA_GAP |
| RB03 | RSP_SPY63 + RV126 |
| RB04 | RSP_SPY63 + VIX_LEVEL |
| RB05 | RSP_SPY63 + RET126 + RV126 |
| RB06 | RSP_SPY63 + RET126 + VIX_LEVEL |
| RB07 | RSP_SPY63 + SMA_GAP + RV126 |
| RB08 | RSP_SPY63 + SMA_GAP + VIX_LEVEL |

RET126与SMA_GAP不得同现；RV126与VIX_LEVEL不得同现。RV21、drawdown252和其他Round5因子不在首轮模型池，失败后不得补入。

## 4. 风险模型 registry

资格单位是 `feature bundle × family selector`，不是单个超参数点。每个 bundle 固定运行以下三个 family selector，共27个risk processes；另有一个不训练的 raw-RSP sentinel。

### 4.1 Positive Ridge

- 训练窗1%/99% winsor、median impute、standardize，所有统计只在训练窗拟合；
- `Ridge(positive=True, fit_intercept=True)`；
- alpha候选固定为 `0.1, 1, 10, 100`；
- inner OOF MAE 的13周moving-block one-SE规则选alpha，更强正则优先。

### 4.2 Additive spline Ridge

- 每个连续输入只建自身二次 spline main effects，不加交互；
- 固定arms为 `n_knots ∈ {3,5}` × `alpha ∈ {1,10}`；
- 相同训练窗预处理；
- inner OOF MAE one-SE后先取3 knots，再取更强正则。

### 4.3 Monotone shallow LightGBM

- `objective=regression`（平方损失），所有风险输入 monotone constraint 为 `+1`；LightGBM 4.6.0不支持 `regression_l1 + monotone_constraints`，因此禁止该无效组合；
- 固定 `learning_rate=0.05`、`min_child_samples=52`、全样本/全特征、不early-stop；
- 四个arms：`max_depth/num_leaves ∈ {(1,2),(2,4)}` × `n_estimators ∈ {50,100}`；
- 固定seed、单线程、deterministic；
- inner OOF MAE one-SE后按 depth1→depth2、50→100 的容量顺序取最简单arm；
- LightGBM wheel版本、平台、依赖hash与确定性测试必须在机器锁前落定。依赖未闭合时不得以 sklearn tree 临时替代。

模型训练loss只负责拟合/inner选择；最终风险资格仍以严格OOS的Y5排序、尾部捕获与稳定性决定。

## 5. Attack comparator registry

Attack侧不开放大规模模型搜索，只冻结三种程序身份：

| ID | 使用A4训练 | 定义 | 身份 |
|---|---:|---|---|
| AX00_Y5_CLEAR_ONLY | 否 | 只读取risk head是否解除；无attack预测 | Round8单标签控制 |
| AX01_RAW_RSP_RECOVERY | 否 | `attack_score=-RSP_defense_score`，阈值只用历史分位数 | 无模型双向RSP sentinel |
| AX02_RSP_A4_MONOTONE | 是 | 输入明确为 `RSP_attack_score=-RSP_defense_score`；对该attack score拟合单调递增 isotonic `E[A4|attack_score]`，越界clip | 唯一正式A4 head |

AX02的输入、实时信息集和AX01完全相同；它只能通过A4学习尺度、非线性平台与自然零收益阈值，不能声称获得新特征信息。不得在看结果后增加RSP Delta4、窗口、binary classifier或LightGBM attack模型。

## 6. 严格时序 folds

- 共同 outer test 年固定为2014–2021的完整执行年度；
- 每个outer fold只使用首个test signal前已经 `target_available_at` 成熟的标签；
- risk/attack共同采用最长13 scheduled weeks purge，并加1 scheduled week embargo；
- outer risk训练至少520个feature-complete、label-mature周；
- inner采用按时间排序的52周validation blocks，最早训练至少260周；每个outer fold至少3个合法inner blocks，否则该process在该fold invalid；在每一个inner边界，训练行还必须满足 `target_available_at < validation_start_signal`，并再次执行13周purge与1周embargo，禁止只在outer层purge；
- 所有winsor、缺失填补、标准化、spline、模型、isotonic和阈值均只在对应train内拟合；
- outer年度只前向预测一次，不在年内用该年度outcome更新；
- 最终预测按日期拼成连续 `outer_oos_head_predictions`，不逐年重置统计或选择状态。

冻结前必须由父日历生成绝对 `folds.json`，逐fold记录训练、purge、embargo、测试与两个target成熟边界。旧R2五周边界不得复用。

## 7. R7资格门

### 7.1 Risk process

所有27个process的primary p统一一次BH-FDR，q<=0.10。正式合格须同时满足：

1. outer覆盖完整、无退化、native/common方向一致；
2. `Spearman(predicted_risk,Y5)>0`、13周block单侧95%下界>0、BH q<=0.10；
3. causal top-quartile Y5 capture>=35%，MAE10 lift>=1.25；
4. 正RankIC完整年度比例>=60%；
5. 六个固定重大事件逐一删除后RankIC均>0；
6. 相对raw-RSP sentinel的RankIC不劣于一个13周block SE（raw score与模型预测尺度不同，禁止直接比较两者MAE）；
7. 多因子process还必须至少满足一项增量：RankIC提高>=0.02、Y5 capture提高>=5个百分点、或MAE10 lift提高>=0.10。

raw-RSP sentinel无论新一轮是否过门都保留为R8控制，但不能冒充本轮新发现。所有合格process继续，不设Top-k。OOS预测绝对相关>=0.95且角色签名相同者形成等价簇；代表按raw/simpler/参数更少/字典序机械选择，不按绩效选择。

### 7.2 A4 head

AX02是唯一正式A4假设，不与27个risk processes共用FDR family。它须满足：

1. outer OOS Spearman>0、4周block单侧95%下界>0；
2. top predicted A4均值>rest，B4 AUC>0.55；
3. W4 median与severe rate不恶化；
4. 六事件留一RankIC均>0；
5. 相对逐outer-train均值预测，OOF MAE有正skill；
6. 相对AX01，RankIC不得下降超过一个4周block SE。

AX00与AX01是控制，不需要通过AX02资格门。AX02失败时，Round8仍可评价Y5-only与raw-RSP控制，但不得生成“已验证attack模型”身份。

## 8. 批次与硬停止

建议四批：

1. `R7A_DUAL_TARGET_FOLDS`：父bundle、共同日历、绝对fold与依赖验收；
2. `R7B_RISK_MODEL_TOURNAMENT`：27个risk processes与raw-RSP sentinel；
3. `R7C_RSP_ATTACK_COMPARATOR`：AX01/AX02严格OOF预测与A4资格；
4. `R7D_HEAD_QUALIFICATION`：等价簇、role ledger与hard stop。

R7D后无条件停止。Round8不能直接用看完全部R7结果后挑出的静态冠军在同一时期声称无偏；它必须按下一份计划在每个policy outer-train内重新执行本资格程序。

~~~text
models_authorized = true          # 仅上述R7模型
strategy_nav_authorized = false
final_state_machine_authorized = false
lockbox_authorized = false
mom255_transfer_authorized = false
~~~

## 9. 冻结前尚需闭合的操作项

以下不是研究方向选择，但必须在 `PREREG_LOCK` 前完成：

- 安装并锁定LightGBM版本、wheel/hash、线程和确定性身份测试；
- 生成绝对folds并证明最长标签成熟/purge；
- 固定27个selector/process ID、12个recipe ID及其跨9个bundle展开的108个trial-arm ID、seed与输出schema；
- 记录R5/R6所有父manifest/tree/file hash，以及R6无候选与用户mechanism override的双重事实；
- 新建Round7 program、registries、batch designs、lock builder与tests；
- 锁前全量测试通过。未经这些步骤，本文件不构成执行授权。
