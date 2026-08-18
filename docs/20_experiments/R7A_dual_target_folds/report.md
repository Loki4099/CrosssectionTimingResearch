# R7A 双target与fold验收报告

R7A 已完成。R4A 五个冻结输入、R5A Y5 与 R6A A4 在 **948个共同周**（2003-08-01至2021-09-24）上闭合；固定2014–2021八个outer folds共 **404个outer-OOS周**。全部inner/outer训练行满足最长13周标签成熟、13周purge与1周embargo；LightGBM 4.6.0重复拟合身份检查通过。

本批未拟合研究模型，也未读取锁箱或生成策略输出。完整工件见 `results/published/round7/R7A/`。
