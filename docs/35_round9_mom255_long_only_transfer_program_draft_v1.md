# Round 9：mom_255_0 long-only迁移计划草案 v1

状态：**路线草案；只接受Round 8冻结并通过的完整状态程序。**

## 1. 唯一问题

Round 9只检验：已冻结市场级risk-veto状态能否改善既有 `mom_255_0` long-only横截面策略。它不是重新选择head、pair、仓位、因子或动量定义的阶段。

## 2. 唯一primary与六格

Primary：

~~~text
mom_255_0 / Top20 / monthly / long-only / equal weight
~~~

固定稳健性面板：

~~~text
Top10 / Top20 / Top50 × weekly / monthly
~~~

其余五格无冠军资格，不能替补Top20 monthly。明确排除WML、short、leverage、其他momentum定义、波动缩放和防御资产。

## 3. 执行会计

- 裸策略保持close信号、下一XNYS open执行、PIT universe、SID字典序破同分与TopK等权；
- overlay每周可更新，基础选股仍按各自weekly/monthly schedule；
- runner使用union event calendar：先公司行动，再在合法base调仓日更新TopK相对账簿，再乘冻结1.0/0.5状态，最后形成一次净股票target vector；
- 同一open只按实际股票 `sum(abs(w_target-w_pre))` 收一次成本，现金不进L1；
- 周overlay日不得偷偷重排名单，月频组合只能在月度base日换股；
- 缺head预测时carry既有overlay，不能阻断裸策略合法调仓；
- K、频率、成本与个股收益不得反向改变市场状态。

六格统一以10bp为主成本，0/5/20bp为压力；另报告与各裸路径原冻结成本的只读桥接表，但不得择优口径。

## 4. Controls与门

每格同时生成：

1. naked `mom_255_0`；
2. frozen state overlay；
3. 同平均实际股票暴露static；
4. risk-only overlay机制control；
5. SPY参考。

Top20 monthly primary须在10bp下同时满足：

- overlay/naked terminal wealth ratio>1；
- 相对同暴露static timing value>0；
- ΔSharpe>0；
- ΔMDD>0。

家族稳健性要求六格全部完成，至少4/6通过上述四项，且weekly与monthly各至少2/3通过；四项的六格中位数也都>0。20bp是方向否决压力，不能产生替补。

若多个Round8 pair通过，则每个pair原样接受同一六格family gate并做预登记的family-wise校正；不得根据mom结果回头改变pair或把多个状态平均。

多pair时的唯一正式检验统计为Top20-monthly在共同周相对其同暴露static control的active log-return均值；使用13周moving-block、固定5,000 draws计算单侧p，全部冻结pair统一Holm FWER α=0.05。六格四项不等式、中位数与20bp方向仍是顺序否决门，不各自产生额外p值，也不能替代Top20-monthly primary。

## 5. 证据与停止

Round 9仍是development transfer，因为相关历史时期已经被研究观察。通过者只能称 `development_transfer_eligible`。所有端到端候选的模型、校准、状态、六格target vector、依赖和hash必须在任何2022–2026 outcome读取前同时冻结。

Round 9结束后硬停。若后续一次性机械锁箱包含多个候选，采用两阶段sealed runner：

1. `prediction_target_phase` 只按时间顺序读取每个信号时点当时已可用的市场/个股历史，允许使用此前已经发生的lockbox价格更新模型状态与TopK，但禁止计算/输出forward label、策略NAV、未来收益或绩效汇总；它一次性写出并hash全部head prediction、market state和下一open portfolio target；
2. 用户另行授权后，`outcome_reveal_phase` 才读取冻结targets对应的执行收益并生成NAV/绩效；任何phase-1 target hash变化即整批invalid。

runner只向研究层暴露phase-1预测/target ledger和审计摘要，不暴露用于选择的lockbox outcome。由于历史价格客观上已存在，这仍只能称mechanical firewall而非纯前瞻确认。多个候选必须在phase 1同时冻结并在phase 2控制family-wise error；看完锁箱再选出的系统只能称 `mechanical_holdout_assisted_selection`。
