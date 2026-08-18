# R7B Risk模型锦标赛报告

R7B 已完成27个冻结 risk processes、216次outer-fold选择与864条inner recipe记录。raw RSP sentinel 在404个outer-OOS周的Y5 RankIC为 **0.3850**。

RSP-only Ridge/GAM/LightGBM的RankIC分别为0.3273/0.2444/0.2760，均未证明优于raw RSP。最佳多因子流程为 `RSP+RV126` positive Ridge：RankIC 0.2052、13周block下界0.0512、BH q=0.0702；但capture、MAE10 lift、年度稳定性和相对RSP增量门未同时通过。

完整预测、inner选择与统计表见 `results/published/round7/R7B/`。
