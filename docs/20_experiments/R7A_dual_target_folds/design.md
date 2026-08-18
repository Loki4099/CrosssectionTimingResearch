# R7A 双target与fold验收：冻结设计

只验证R4A特征、R5A Y5、R6A A4父bundle身份，物化共同周与绝对nested folds。每个inner/outer边界均按Y5最长13周执行purge，并加1周embargo；训练标签必须在validation/test首个signal前成熟。2014–2020为完整outer年度，2021固定截止2021-09-24。

本批不拟合模型、不生成prediction/state/NAV。父hash、fold counts、共同样本或LightGBM确定性任一不匹配即停止。
