# Round 7：双头模型实验决策备忘录

Round 7 已按预注册计划完成并在 R7D 硬停。结论不是“RSP 失效”，而是：**冻结的 27 个 Y5 风险模型没有一个在严格 outer-OOS 下同时满足全部资格门；A4 监督的 RSP 单调进攻 head 也没有通过全部资格门。** 因而 Round 8 尚未自动授权。

## 风险头

2014–2021 的 404 个严格 outer-OOS 周中，raw RSP sentinel 的 Y5 RankIC 为 **0.3850**。三个仅使用 RSP 的学习模型仍有明显风险排序能力：Ridge 0.3273、LightGBM 0.2760、GAM 0.2444，13周 block 下界均为正；但它们都比 raw RSP 差超过预注册的一个成对 block SE，因此不能称为模型改进。

最佳多因子流程是 `RSP + RV126` 的正系数 Ridge：RankIC **0.2052**、13周 block 下界 **0.0512**、BH q **0.0702**。它仍未通过，因为 causal top-quartile Y5 capture 仅28.53%、MAE10 lift 仅1.148、完整年度正RankIC比例33.33%，并且相对 raw RSP 没有达到预注册增量门。其他加入 RET126、SMA gap 或 VIX 的流程也未同时改善排序、尾部捕获和年度稳定性。

这说明潜力因子作为风险传感器的解释仍成立，但在本轮有限样本、冻结模型和Y5目标下，它们没有证明能改善 RSP 的严格 OOS 风险头。尤其，直接用 Y5 的绝对误差训练模型并不自动改善 RankIC；训练损失与最终排序/尾部门之间仍存在目标错配。

## 进攻头

raw `-RSP` 对 A4 的 outer-OOS RankIC 为 **0.1813**，4周 block 下界0.0460，B4 AUC 0.6269；这再次支持 RSP 同时包含风险解除/重新进攻信息。

单调 isotonic `E[A4|-RSP]` 将 RankIC 小幅提高到 **0.1899**，4周 block 下界0.0591，六事件留一最小RankIC 0.1644，W4中位数和严重路径率均未恶化。但其 B4 AUC 降至0.6129，且相对逐 outer-train 均值预测的 MAE skill 为 **-1.08%**，因此按冻结的交集门失败。它没有创造新的特征信息，只显示 A4 标签可对同一个 RSP 输入做有限的非线性排序校准。

## 当前判断

1. raw RSP 仍是当前最强、最简洁的风险核心；本轮提高了对其跨标签机制稳定性的信心，但没有证明“用模型重新拟合 RSP”更好。
2. RET126、RV126、SMA gap、VIX level 可以保留为风险解释与未来条件交互候选，但本轮没有获得自动进入状态机的模型资格。
3. A4 head 有方向信息但未通过校准门。后续若继续，必须把 `Y5-only clear`、raw `-RSP` 和 isotonic A4 head 明确区分为控制、机制候选和未合格模型，不能把后者改写为已验证进攻模型。
4. Round 8 若启动，应另行冻结。它可以将 raw RSP/Y5-only 作为强制控制，并把本轮流程作为需要在每个 policy outer-train 内重新执行的完整选择程序；不能在看过本轮 outer 结果后只挑静态赢家并声称无偏。

本轮未读取2022–2026 outcome，未生成最终状态机、策略NAV、锁箱预测或 mom255 转移结果。程序状态为 `completed_pending_user_round8_freeze_decision`。
