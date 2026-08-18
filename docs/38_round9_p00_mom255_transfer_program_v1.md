# Round 9：P00 对 mom_255_0 long-only 的迁移检验 v1

状态：**预注册开发实验；锁箱关闭。**

Round 9 只回答一个问题：Round 8 唯一合格的 `P00_RSP_Y5_CLEAR` 市场状态，能否在不改变横截面排名的情况下，降低 `mom_255_0` long-only 的风险并改善净表现。

唯一 primary 是 `Top20 / monthly / equal-weight / long-only`。`Top10/20/50 × weekly/monthly` 六格必须全部完成；其余五格只检验可迁移性，不能替补 primary。所有格统一以 10bp 为主成本，0/5/20bp 为压力。WML、融券、杠杆、其他动量、阈值搜索、仓位搜索和重新训练 P00 均禁止。

共同开发样本固定为 2018-01-02 至 2021-12-31。P00 只读取截至 2021-09-24 的冻结外层 OOS 信号，2021-09-27 后把最后状态持有到样本末；任何 2022–2026 价格或结果读取均禁止。

执行使用联合事件日历：公司行动先于开盘交易；基础名单只在 G00 原合法调仓日更新；P00 可在其周度执行日缩放总股票暴露；同一开盘最终只形成一个净股票目标并按实际股票 L1 收一次费用。月频组合在周度 overlay 日不得重新排名。

每格生成 naked、P00 overlay、同平均实际股票暴露 static 三条路径。static 的常数目标仓位由零成本 dynamic 路径的平均日度实际股票暴露，用固定 48 次二分法机械求得，之后原样用于所有成本情景。

Round 9 首先要求新的 naked runner 在共同样本上逐日复现冻结 G00。之后 primary 在 10bp 下必须同时满足 overlay/naked 终值比大于 1、相对 static 的 timing value 大于 0、Sharpe 增量大于 0、最大回撤改善大于 0，并通过 13 周 moving-block 单侧检验。家族还要求至少 4/6 格通过四项经济门、weekly 与 monthly 各至少 2/3、六格四项中位数均为正，且 20bp timing 不翻负。

通过者只能称 `development_transfer_eligible`。R9C 后硬停；不得自动打开机械锁箱，也不得依据结果修改 P00、TopK、频率、仓位或门槛。
