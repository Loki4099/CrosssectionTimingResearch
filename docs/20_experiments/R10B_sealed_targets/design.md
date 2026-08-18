# R10B — sealed prediction and target phase

R10B 只能逐时间点构造 raw RSP score、年度 expanding q75、P00状态及六格下一开盘股票target。2021Q4作为状态桥接；锁箱targets从2022-01-03开始，到2026-06-29结束。

目标账簿生成允许使用当时已经发生的RSP/SPY历史、G00冻结基础名单、当日开盘价格和先前公司行动，以维持真实的相对持仓组成；禁止计算总资产、日收益、forward return、cost scenario、Sharpe、MDD或任何绩效汇总。月频组合在周度P00事件不得重排。输出完成后必须冻结manifest与完整target ledger哈希并硬停。
