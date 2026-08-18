# R6D Attack4 稳健性与停止：冻结设计

状态：**development decision gate / unconditional hard stop after completion**

父计划：[Round 6 Attack4 单因子角色审计计划](../../31_round6_attack4_single_factor_program_v1.md)

## 1. 只读输入

本批只读取不可变的 R6A target、R6B 单因子统计与 R6C 固定经济代理。不得重算方向、替换 target、修改阈值或新建交易规则。

父 bundle 的文件 bytes、SHA256、resolved config 与防火墙标记必须全部匹配；任一不匹配即 invalid_method 并停止。

## 2. 审计矩阵

对全部20个 arm 统一审计：

1. arm-native 与20-arm common sample；
2. 4周 moving-block primary，2,000 draws；
3. 20项 primary BH-FDR；
4. 8周 moving-block veto；
5. 完整年度方向与经济贡献；
6. GFC、COVID及机器 registry 所列 major events 逐一删除；
7. 单一事件正贡献集中度；
8. 0/5/10/20bp成本；
9. RSP low-risk 条件统计；
10. coverage、缺失连续长度与阈值历史充足性。

8周 block 不是第二 primary：它不能晋级任何 arm，也不进入 BH family；它只能否决4周 primary 候选。

## 3. 最终三 route gate

R6D 分别应用并报告以下身份，禁止把三条 route 混成一个事后综合分：

### robust_direct_attack

仅 `direct_eligible=true`：reference五项通过；4周 block 单侧95% lower>0；BH q<=0.10；positive-upside capture>=35%；top mean A4>0；B4 lift>=1.10；binary/W4 guardrails通过；native/common同向；事件留一 rho>0；8周 rho 点估计>=0。

### economic_reference

仅 `context_only=false`：10bp与20bp相对 matched-exposure active terminal>0；正主动贡献年度>=60%；动态 MDD 不差于 matched static；binary/W4 harm veto未触发。该身份不改写边际统计结论。

### conditional_eligible

仅 `conditional_eligible=true`：必要 cell 同时>=共同周5%且>=44周；RSP low-risk 条件内 A4 contrast 的4周 block 单侧95% lower>0；conditional family BH q<=0.10；同条件 W4 harm veto未触发。该身份只表示条件角色。

机器结果另给 `model_input_eligible = robust_direct_attack OR economic_reference OR conditional_eligible`；`context_only` 永不自动晋级。

## 4. 无 Top-k

- 不排名后选 Top1/Top3/Top5；
- 所有通过任一获准 route 的 arm 均保留 route-specific 身份，并按 union 记录 model_input_eligible；
- 未通过者保留完整结果与失败原因；
- 不允许 reference-only 补位；
- 合格数量只决定是否值得起草 R7，不改变 R6 的授权边界。

## 5. Hard stop

若无 `model_input_eligible` arm：

~~~text
status = completed_development
assessment = completed_no_attack_role_candidate
models_authorized = false
lockbox_authorized = false
final_state_machine_authorized = false
mom255_transfer_authorized = false
~~~

若至少一个 arm 为 `model_input_eligible`：

~~~text
status = completed_development
assessment = completed_attack_role_candidates_development_only
models_authorized = false
lockbox_authorized = false
final_state_machine_authorized = false
mom255_transfer_authorized = false
~~~

两种结果下 R6D 均立即 hard stop。不得在本轮：

- 训练或比较模型；
- 增加/删除因子；
- 修改 A4、B4、W4；
- 改4周 primary或8周 veto；
- 改q75、仓位、成本或RSP条件；
- 提升 runner-up；
- 读取2022–2026 lockbox；
- 生成最终 risk-veto 状态；
- 读取或运行 mom_255_0。

只有新的、结果后明确标注且在运行前冻结的 R7 设计，才可请求模型开发授权；R6 阳性本身不构成授权。

## 6. 输出

本批输出 gate matrix、qualification ledger、失败原因、共同/原生样本对照、4周/8周 block、BH、年度、事件、成本、RSP条件统计及不可变 manifest。

本目录不创建结果报告；正式运行完成后报告必须另行生成，并不得回写本设计。
