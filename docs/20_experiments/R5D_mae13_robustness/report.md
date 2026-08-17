# R5D MAE13 稳健性报告

状态：`completed_development`；程序按预注册硬停止。

17项中有2项达到 `reference_positive`：RSP/SPY63与SMA gap；只有RSP/SPY63达到 `robust_reference_positive`。

RSP/SPY63在874个共同开发周上的Spearman仍为0.2098。逐一删除与六个>=10%回撤episode重叠的13周标签后，Spearman范围为0.1721–0.3019，始终为正；因此结果不是由dot-com恢复尾段、GFC、2015、2018或COVID任一事件单独创造。主成本下active terminal为+35.42%，动态MDD优于同期always-SPY。

SMA gap虽有正向排序、49.81% loss capture和+6.54%同暴露主动终值，但block下界为负、FDR q=0.200，因此只保留为非稳健参考。

Bundle manifest SHA256：`29bc9ef9a872260596027bd2f38e01a92c4a1e6da50507641b30aeacc285abbc`；tree SHA256：`e5abebca413810496ef85a397a5f047a6cfc41de55cdaca48cc90131bebb0a62`。所有bundle文件hash匹配，同run-id重跑被不可变目录拒绝，锁箱未读取。
