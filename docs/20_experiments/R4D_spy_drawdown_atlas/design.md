# R4D：SPY 大回撤与反例事件图谱——冻结设计

- 状态：`preregistered_development_authorized`
- 区间：SPY total-return close，1993-01-29 至 2021-12-31
- 推断单位：episode，不是重叠周

## 事件算法

用前向 running high 建链：从最后 running-high peak 起，直到首次恢复该 peak 只算一个 episode。保存 first -5/-10/-15/-20 breach、trough、recovery；未恢复到 cutoff 为右删失。主事件 max drawdown<=-10%，-15/-20 为层级。5–10% episode 是 shallow control；record high 后26周 MAE<5% 为 calm peak；非事件 matched normal week 使用 seed=20260817 机械抽取。anchor 映射为不晚于事件日期的最近计划 signal close。

## 因子轨迹

对 17 条 eligible factor，只用每个时点之前的历史生成 expanding percentile 与 expanding z-score，最少260周；不得使用全样本变换。报告 `[-52,-26,-13,-8,-4,-1,0]` 周及 breach/trough/recovery 附近轨迹、首次 q75 alert 相对 peak/first-10 lead、非事件 alert episodes/year、trough 后 clearance lag、误报及 leave-one-event-out。图形不能产生冠军。

输出 `episodes.csv`、`event_anchors.csv`、`factor_event_paths.parquet`、`event_summary.csv`、`figures/*.png`、`manifest.json`。2022+ 事件和 outcome 禁止进入。
