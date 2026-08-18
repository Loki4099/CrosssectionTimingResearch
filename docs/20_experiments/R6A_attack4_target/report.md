# R6A Attack4 target 报告

R6A 已按预注册完成，状态为 `completed_development`。连续 A4 逐值复用冻结 R3B open-to-open 四周 SPY 相对现金 log return，B4 严格等于 `1[A4>0]`，W4 逐值复用冻结 R2B 四周最差路径。

2005–2021 共有883个成熟 development 周，信号期为2004-12-31至2021-11-26。A4 均值0.677%、中位数1.383%，正值占66.36%；W4均值-2.760%，低于 `log(0.95)` 的严重周占14.27%。A4 lag-1 自相关为0.726，后续推断已按冻结的重叠周 block 处理。

2022-01-03及以后 execution 的 A4、B4、W4 均未物化；本批没有读取锁箱 outcome，没有训练模型或生成策略路径。
