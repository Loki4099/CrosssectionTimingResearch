# R10C — mechanical outcome reveal

R10C 只接受 R10B 已哈希的 P00 状态与六格股票targets。P00 overlay逐事件使用该target ledger，不得重新计算状态、阈值或目标。naked使用冻结G00基础账簿，matched-static使用Round 9在揭示前冻结的六格常数仓位；三条路径共享同一执行引擎、公司行动、RF、实际股票L1与0/5/10/20bp。

唯一primary仍为Top20-monthly 10bp。四项primary经济门、六格family门、20bp方向门与13周moving-block门全部沿用Round 9。事件稳健性改为预先确定的leave-one-calendar-year-out，避免在看到锁箱结果后定义事件窗口；每个剔除年度后的primary timing必须为正。

输出后硬停。失败不得换TopK、频率、阈值、仓位或候选；成功也只称mechanical lockbox通过，不改写免费PIT与已观察G00历史的证据边界。
