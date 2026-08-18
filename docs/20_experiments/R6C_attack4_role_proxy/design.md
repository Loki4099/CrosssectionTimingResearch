# R6C Attack4 角色经济代理：冻结设计

状态：**development economic ruler only / not final state machine**

父计划：[Round 6 Attack4 单因子角色审计计划](../../31_round6_attack4_single_factor_program_v1.md)

## 1. 目的

本批只用一把固定机械测量尺回答：单因子 attack score 高时多持有 SPY，是否相对同平均暴露静态控制具有成本后增量。

该测量尺不是最终 risk/attack 状态机，不授权模型、pair、滞回、联合阈值、仓位优化、lockbox 或 mom255。

## 2. 唯一 attack 测量尺

对每个 arm、每个 execution year：

~~~text
attack_score > causal historical q75  -> target SPY weight 1.00
otherwise                             -> target SPY weight 0.50
~~~

规则：

- q75 只使用该 execution year 之前的信息；
- 至少260个历史有效周；
- 严格大于；等号归50%；
- score 缺失时沿用上一目标，不产生 overlay trade；
- 每周下一 XNYS open 恢复目标；
- 现金按冻结逐日 RF 复利；
- SPY 权重仅允许0.50或1.00；
- 禁止 short、leverage 和其他防御资产。

## 3. 会计与控制

- 主成本：每美元 SPY 实际 L1 交易额10bp；
- 压力：0/5/20bp；
- 路径从共同首个合法 execution open 以全现金 NAV=1 启动一次；
- 年度边界携带财富和持仓，不清仓、不重置；
- 主控制：同日历、同成本、同平均实际 SPY 暴露的静态 SPY/cash replay；
- 另报告 always-SPY，但不能替代 matched-exposure 控制。

指标：

- active terminal wealth；
- CAGR、与 Round5 一致的未扣 RF 日收益 Sharpe、MDD、年化波动、beta；
- 平均 SPY 暴露、turnover、成本；
- 完整年度 active contribution；
- missed-upside、defense-benefit 与净 timing，仅作机制归因。

## 4. RSP 低风险条件统计

RSP low-risk 固定为：

~~~text
RSP_SPY63 defense score <= its causal historical q75
~~~

q75 使用相同至少260周、按 execution year 冻结的规则。只在 low-risk 周报告：

- A4 均值与中位数；
- B4 rate；
- W4 median；
- severe_W4 rate；
- 每个 attack arm 的 top-quartile 与 rest 差；
- 年度覆盖和事件覆盖。

这些统计不能产生 RSP×attack 联合仓位，不得搜索 RSP 阈值，也不得被称为最终 risk veto。

## 5. 两条独立 Role-proxy 门

### 5.1 economic_reference

`context_only=false` 的 arm 必须同时满足：

- 10bp与20bp相对 matched-exposure active terminal wealth>0；
- 至少60%完整年度 active contribution>0；
- 动态 MDD 不差于 matched-exposure static；
- binary/W4 harm veto 未触发。

该身份是经济参考，不宣称 R6B 边际统计发现；它可以按预注册 union 进入下一轮模型输入。

### 5.2 conditional_eligible

仅机器 registry 中 `conditional_eligible=true` 的 arm 参加。以 RSP low-risk 内 attack-high 与 attack-not-high 的 mean A4 差为 primary contrast：

- 每个必要 cell 占共同周至少5%且不少于44周；
- 使用4 scheduled weeks moving-block、2,000 draws；
- 单侧95% lower bound>0；
- 全部注册 conditional arms 统一 BH q<=0.10；
- 同条件 W4 median 与 severe_W4 rate不恶化。

该身份只能称 conditional role，不能称 direct champion。它可以在 R6B 边际 primary 失败时独立成立，这正是检验树模型潜在条件信息的预注册路径。

## 6. 输出与授权

输出逐 arm states、target weights、turnover、NAV、matched controls、年度贡献、成本压力、机制归因与 RSP low-risk conditional table，并写入不可变 manifest。

~~~text
models_authorized = false
lockbox_authorized = false
final_state_machine_authorized = false
mom255_transfer_authorized = false
~~~
