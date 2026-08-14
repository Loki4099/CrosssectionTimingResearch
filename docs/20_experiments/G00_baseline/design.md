# G00：裸动量双组合基线设计

状态：设计已冻结；数据已冻结；旧运行已归档；`g00-frozen-v3-v1` 已完成并通过 free-research 验收。结果见 [report.md](./report.md)。

## 假设

同一个 S&P 500 PIT 横截面动量排序应分别产生：

1. 保留市场 beta 的 TopK long-only 收益；
2. 提取赢家相对输家价差的 dollar-neutral WML 收益。

G00 是九宫格所有策略的唯一共同对照，不包含波动率择时或反转切换。

## 共用口径

- 动量：`mom_255_0`、`mom_255_21`、`mom_12_1`；
- K：10、20、50；
- 频率：周频、月频；
- 信号：期末最后交易日收盘；执行：下一交易日开盘；
- 组合内部每次计划调仓恢复等权；
- long-only：TopK 合计 +100%；
- long-short：TopK 合计 +50%，BottomK 合计 -50%，gross=1、net=0；
- 同时派生 gross=2 传统 WML；
- 成本：0/5/10/20bps；月频主 5bps、周频主 10bps；
- 借券费：0%/1%/3%年化场景；
- 评价期：2018-01-02 开盘至 2026-06-30 收盘；
- 业绩基准：当前原型使用 SPY 总回报作为 S&P 500 的可投资代理，不冒充官方 SPXTR；
- 数据质量排除：冻结 v3 已恢复并验证 canonical `sec::COL`，策略层固定排除清单为空；
- WML 缺开盘价：新目标缺价，或仍属于当期 PIT 股票池的现有持仓临时缺价，整次篮子调仓取消并保留原持仓；已不属于当期 PIT 股票池的终止旧仓，只能按冻结数据中已审计的公司行动处理，或在此前不超过 5 个权威交易日的最后有效收盘价显式清算。未来 25 个交易日内已有冻结公司行动的持仓应保留至行动日。所有清算、回退日期、换手和成本必须审计，不允许单腿静默成交。

共 `3 × 3 × 2 × 2 = 36` 条核心持仓路径。成本、借券费和 gross=2 展示为同一路径的场景或派生口径，不增加信号路径数。

## 放行条件

- 先在冻结数据上从头生成 18 条 long-only、72 个成本场景的基线，再由统一 G00 runner 逐日复现该同数据版本基线；
- 每个 WML 调仓点目标 gross=1、net=0，且 TopK/BottomK 无重叠；
- 分腿收益、交易成本、借券费、gross/net、SPY beta 与公司行动均可审计；
- 任一空头缺价或未支持公司行动必须显式失败或进入异常报告，不能静默忽略；目标缺价记录 `skipped_signed_missing_open`，终止旧仓近似清算记录 `executed_with_terminal_last_close` 及 SID/回退日期；
- WML 不设与组合宽度或调仓次数无关的固定“最多一次”阈值；每次 `skipped_signed_missing_open` 必须由同一开盘已登记的冻结公司行动解释，且旧持仓不得缺价；`skipped_pending_corporate_action` 只允许保留至冻结账本中已知的未来行动日。受影响路径必须在后续计划调仓恢复，永久冻结或无证据跳过一律失败。

当前有效报告为 [report.md](./report.md)，且只对应冻结 v3 数据与 `g00-frozen-v3-v1`。

## 下一次从头运行

High 应按以下顺序执行，且不得复用归档中的旧 bundle：

1. 在冻结 v3 数据上运行 `run-baseline`，生成全新的 18 路径、72 场景 long-only 基线，建议 run id 为 `g00-long-only-frozen-v3`；
2. 将该新目录作为 G00 的 `--legacy-baseline-root`，运行 `config/experiments/G00.toml`，建议 run id 为 `g00-frozen-v3-v1`；
3. 验证 72 个 long-only 逐日复现控制、216 个 long-short 场景、公司行动和终止事件审计后，才生成新的 `report.md`；
4. G00 未通过前不得启动 G11–G33。

两个命令都必须显式指定冻结数据版本并传入 `--allow-review-dataset`。
