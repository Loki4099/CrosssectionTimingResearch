# R5B MAE13 单因子：冻结设计

只使用 `config/experiments/round5/factor_registry.csv` 的17条 arm 与 R5A 冻结 target。公式、方向、窗口、native span及共同样本均不可更改。

主指标为 `Spearman(defense_score,Y5)`；辅项为五分位单调性、top-25% loss capture、top-vs-rest严重度、MAE5/10分类诊断与误报。推断用13周 block bootstrap并对17项做BH-FDR。该批不生成模型或组合，不读取2022+。
