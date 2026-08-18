# R7C RSP Attack comparator报告

R7C 已完成。raw `-RSP` 的A4 outer-OOS RankIC为 **0.1813**、B4 AUC为0.6269；raw控制不是A4收益尺度预测，因此不解释其MAE。单调 isotonic `E[A4|-RSP]` 的RankIC为 **0.1899**、4周block下界0.0591、B4 AUC为0.6129；W4路径否决未触发，但相对逐outer-train均值的MAE skill为 **-1.08%**。

本批只比较同一RSP输入的raw分数和预注册单调校准，没有增加特征或搜索attack模型。完整工件见 `results/published/round7/R7C/`。
