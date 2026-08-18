# R9A — mom255 union-event ledger

读取冻结 G00 六格 `mom_255_0` long-only 目标账簿与 Round 8 P00 周状态。构造 base execution 与 P00 execution 的并集日历；先应用公司行动，再在 base 日更新冻结名单，在 overlay-only 日保持当时相对组成，仅缩放总暴露；同日只执行一个净目标并收一次实际股票 L1 成本。

输出六格、三路径、四成本的日 NAV、事件账簿、目标与交易账簿。naked 路径必须在 2018-01-02 至 2021-12-31 逐日复现 G00 对应成本路径，容差由 program 固定。任何身份失败使整批 invalid。
