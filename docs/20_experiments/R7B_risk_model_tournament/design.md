# R7B Risk模型锦标赛：冻结设计

严格读取R7A folds，运行9个冻结feature bundles × 3个family selectors。每个selector只在inner folds的4个固定recipe中用OOF MAE与13周block one-SE选择；outer test只生成一次预测。所有27个process统一BH-FDR并输出完整prediction/selection ledger。

资格门逐项采用Round7主计划第7.1节；raw RSP只作控制。不得增加recipe、改变目标、使用outer outcome调参或生成策略NAV。
