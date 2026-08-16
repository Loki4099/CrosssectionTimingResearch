# R2B_SIGNAL_DIAGNOSTICS：目标与低维市场信号——冻结设计

状态：**设计冻结；尚未生成 target 或 feature 表。** 关联总计划：[Round 2 v1](../../23_round2_defense_timing_signal_program_v1.md)及[锁箱 outcome 防火墙修订](../../23_round2_defense_timing_signal_program_v1_amendment_1.md)。本批只授权从冻结 R2A 免费长样本核心线构造周频目标、特征和无模型的 development 信号诊断；禁止训练监督模型、产生策略 NAV、生成 mechanical lockbox target 值或修改第一轮数据。

## 1. 输入锚与范围

- R2A snapshot：`r2a-long-free-20260816-v1`
- dataset：`round2-market-core-1993-2026-v1-candidate`
- manifest SHA256：`071055016268d83f60a03b70be498d85da07897d290b049e4ed7524d1b9e674c`
- 16-file tree SHA256：`6985176ea1088d70c0191d6e24527dc7117e66ce81a1c0ece7ad7f539ed061ce`
- fold manifest：`config/experiments/round2/folds.json`
- 周频：每个 W-FRI 周期最后一个 XNYS session 收盘计算，下一 XNYS session 开盘执行；不得硬编码周五或周一。
- 首个 feature-complete signal：1994-01-28；development outer：2005–2021；mechanical lockbox：2021-12-31 signal / 2022-01-03 open 至 2026-06-26 signal。

VIX 官方源缺 1997-01-31 周信号，F3 固定为 `invalid_data/not_available`，不得填补或换源。Norgate/PIT/F4/F5/PIT01 按用户决定不开放，不以其他特征补位。

## 2. 日收益与特征时点

令 `P_t=SPY tr_close[t]`，`r_t=P_t/P_(t-1)-1`。所有滚动窗口按 XNYS session 计数并包含 signal session 当日收盘；只有 `available_at <= signal_timestamp_et` 的记录可用。特征不得读取同日 French RF，因而 F1 使用 SPY total return 而不是事后同日超额收益。

固定 core 特征：

```text
spy_total_return_21d        = P_t / P_(t-21) - 1
spy_total_return_126d       = P_t / P_(t-126) - 1
sma50_over_sma200_minus_1   = mean(P_[t-49:t]) / mean(P_[t-199:t]) - 1
drawdown_from_252d_high     = P_t / max(P_[t-251:t]) - 1
spy_rv21                    = std(r_[t-20:t], ddof=1) * sqrt(252)
spy_rv126                   = std(r_[t-125:t], ddof=1) * sqrt(252)
log_spy_rv126               = log(spy_rv126)
log_rv21_over_rv126         = log(spy_rv21 / spy_rv126)
downside_variance_share_63d = sum(min(r,0)^2) / sum(r^2), window 63
return_skew_63d             = adjusted Fisher-Pearson sample skew, window 63
return_excess_kurtosis_126d = unbiased Fisher excess kurtosis, window 126
```

方差分母为零、RV 非正、价格非正或非有限值时对应周 invalid；不设置经验性零替代。模型输入为九项 core（不含 `spy_rv21` level）；`spy_rv21` 仅作为 sentinel。模型训练窗内固定先按每列 1%/99% 分位截尾，再以训练中位数填补，最后以训练均值/样本标准差标准化；所有 fitted transform 逐 outer/inner fold 保存。若完整 core 周出现缺失，整周不进入任何候选，所有候选使用共同周。

## 3. 三个目标

目标从可成交执行开盘开始，均为模型无关毛市场结果：

```text
T1 = log(TR_open[e_(t+1)] / TR_open[e_t])
     - sum(log(1+rf_d), d in [e_t,e_(t+1)))
T2 = 1[T1 < 0]
T3 = min(0,
         log(TR_close[d]/TR_open[e_t]) - sum(log(1+rf_j), j in [e_t,d])
         for every session close d before e_(t+4),
         log(TR_open[e_(t+4)]/TR_open[e_t])
           - sum(log(1+rf_j), j in [e_t,e_(t+4))))
```

T1/T2 的 `target_available_at` 为下一周执行开盘与相关 RF/价格实际可读时点的最大值；T3 为第四周执行开盘及全部中间记录可读时点的最大值。末尾 1/4 周分别 censored，绝不填补。T2 是唯一模型选择目标；T1/T3 只评价同一 T2 防御分数。

## 4. 四条 sentinel

所有 raw score 预先定向为越高越应防御：

| ID | raw defense score |
|---|---|
| `SENT_RV21` | `log(spy_rv21)` |
| `SENT_SMA_GAP` | `-sma50_over_sma200_minus_1` |
| `SENT_DRAWDOWN252` | `-drawdown_from_252d_high` |
| `SENT_RET21` | `-spy_total_return_21d` |

不得先二值化 sentinel。`p_cash_wins` 只可由 R2C 冻结的 inner-OOF Platt 映射产生；R2B 不按样本表现挑阈值。

## 5. R2B 信号诊断

对每项 core 特征及四条 sentinel，只在 pre-lockbox development 保存：T1/T3 Spearman 与 Pearson time-series IC、T2 ROC-AUC/PR-AUC、训练期冻结 T3 q10 的尾部分类、分数五分位内 T1/T2/T3 单调表。R2B 只陈述方向、缺失和分期稳定性，不宣称 champion。

主推断为固定 seed 20260816 的 2,000 次、13 周 moving-block bootstrap，4/26 周为敏感性；禁止 IID t-test。development 危机窗口冻结为：dot-com 2000-03-24..2002-10-09、GFC 2007-10-09..2009-03-09、COVID selloff 2020-02-19..2020-03-23。COVID rebound 2020-03-24..2020-08-18 只作再进攻诊断。2022 bear 2022-01-03..2022-10-12 已预登记，但只在唯一 candidate 的 lockbox 中解封。

## 6. 因果与质量门

1. 未来价格或 RF 改动不得改变此前 feature；signal 前数据改动可按公式改变当期和后继。
2. 每个 target 的全部开盘、收盘和 RF 路径 100% 覆盖；T3 任一点缺失则该行 invalid，不插值。
3. `target_available_at <= prediction_signal_timestamp` 才可进入训练；统一边界再排除 validation/test 前 5 个 scheduled signals。
4. features/targets 主键均为 `signal_session`，日期唯一递增；公式独立复算误差不超过 `1e-12`。
5. 不读取或修改 R2A、v3、G 组 bundle；输出目录已存在即拒绝覆盖。

## 7. 输出合同

不可变 R2B bundle 至少包含：

```text
features_weekly.parquet
targets_weekly.parquet
sentinel_diagnostics.parquet
feature_coverage.parquet
config_resolved.toml
manifest.json
```

`features_weekly` 可覆盖完整日历。`targets_weekly` 对 2021-12-31 及以后 signal 只能保存键、路径端点/成熟规则、`withheld_lockbox=true` 与空 target 值；pre-lockbox signal 的 T1/T2 或 T3 若在该 signal close 后才成熟，也按各自 availability 留空。不得以隐藏列、另一个文件或日志保存真实值。manifest 必须记录 R2A/folds/program/amendment/design/config/code/dependency SHA、行数、schema、bytes 与文件 SHA；`predictions_oos`、模型对象、策略 NAV 和 lockbox champion 结论在本批均为禁止输出。
