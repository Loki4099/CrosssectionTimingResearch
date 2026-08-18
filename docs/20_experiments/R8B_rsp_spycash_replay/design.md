# R8B SPY/cash replay：冻结设计

只读取R8A冻结状态，在2014-01-06至2021-12-31运行1.0/0.5 SPY/cash路径及各自matched-static；成本固定0/5/10/20bp，主成本10bp。不得改变状态、生成mom255或读取锁箱。
