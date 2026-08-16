# R3A_ASYMMETRIC_REENTRY 实验报告

状态：**development 已完成；没有 re-entry candidate。锁箱与 `mom_255_0` 迁移继续关闭。**

本报告只对应不可变运行 `r3a-asymmetric-reentry-development-v1`，评价期为 2005-01-03 open 至 2021-12-31 close。2021-12-31 signal 起的 235 周 mechanical lockbox 没有生成状态、目标、NAV 或指标。

## 一句话结论

用“连续两个收盘站上 SMA21”提前恢复进攻，确实把错失上涨减少了 **68.7%**，但也把防守收益从 `3.7132` 降到 `1.0773`，减少得更多。它相对原对称 RV21 规则终值落后 **14.16%**，只保留 **5.43%** 的最大回撤改善，且落后同平均暴露静态控制 **2.81%**。H1–H4 全部失败，因此这条简单价格恢复出口被淘汰。

## 1. 运行与审计锚

- Bundle：`C:\Users\17866\QuantWork\MomentumRversionMethod-runtime\results\experiments\round3\R3A_ASYMMETRIC_REENTRY\runs\r3a-asymmetric-reentry-development-v1`
- Manifest SHA256：`6d399a8dc06c7718c5a5f2ae5391b5f62da588259a26412b2e2d582083abb332`
- 9-file tree SHA256：`259fa664b83365c7112fa3fda7bd369347cd16ba4c55f8779889c50c6f887a68`
- 运行代码 commit：`aaa7ddc`
- R2A manifest/tree：`071055016268d83f60a03b70be498d85da07897d290b049e4ed7524d1b9e674c` / `6985176ea1088d70c0191d6e24527dc7117e66ce81a1c0ece7ad7f539ed061ce`
- 预注册机器锁：`e32de6b95197758132c22b0a4181b307cf92b05b884fa409016aa37fe2c1c1e9`；amendment 1 只移除了评价期外的 dot-com leave-out 门，未使用 R3A 结果。
- `formal_eligible=false`，仍是免费数据 development 证据。

Bundle 恰有 9 个文件，manifest 记录其余 8 个；逐文件 bytes/SHA、schema、键、计数、有限性和正 NAV 均通过。相同 run-id 重跑在读数据前抛出 `FileExistsError`，重跑前后 tree SHA 完全不变。

## 2. 状态与暴露

共有 887 个周信号：首个 signal 为 2004-12-31、首个执行为 2005-01-03；最后 signal 为 2021-12-23、最后执行为 2021-12-27。逐日 NAV 每条路径 4,280 行。

| 指标 | 对称 RV21 | 非对称 re-entry |
|---|---:|---:|
| 防守周数 | 246 | 73 |
| 平均实际 SPY 权重 | 86.18% | 95.89% |
| 进入防守 | — | 28 |
| 价格恢复退出 | — | 27 |
| re-arm | — | 27 |
| vol-only false-alarm 退出 | — | 7 |

非对称规则明显更快恢复满仓，但这并没有提高择时价值；它主要把策略重新推近 always-SPY。

## 3. 主经济结果（10bp）

| 策略 | CAGR | Sharpe（ex-RF） | MDD | 年化波动 | beta | 平均 SPY 权重 |
|---|---:|---:|---:|---:|---:|---:|
| Always SPY | 10.49% | 0.553 | -55.20% | 19.29% | 1.000 | 100.00% |
| 对称 RV21 | 10.96% | 0.744 | -33.02% | 13.68% | 0.668 | 86.18% |
| 非对称 re-entry | 9.97% | 0.558 | -54.00% | 17.77% | 0.898 | 95.89% |

相对对称 RV21，非对称规则：

- 终值 `-14.16%`；
- CAGR `-0.993pp`；
- Sharpe `-0.186`；
- MDD 恶化 `20.02pp`；
- 对称规则相对 always-SPY 改善 MDD `22.18pp`，非对称规则只改善 `1.20pp`，保留率 **5.43%**。

因此不能把结果表述为“重新进攻恢复了收益且保留防守”。它基本恢复了 beta，却没有恢复有效 timing。

## 4. 是否只是暴露差异

非对称规则的同平均暴露静态权重为 `95.889%`，同实现超额波动静态权重为 `92.196%`。

| 诊断 | 终值相对财富 |
|---|---:|
| 非对称 / 对称 RV21 - 1 | -14.16% |
| 非对称 / 同平均暴露 - 1 | -2.81% |
| 非对称 / 同波动 - 1 | +1.87% |

同波动比较略为正，但预注册要求它必须同时通过同平均暴露、机制、回撤和稳健性门；其余条件均失败。单个正结果不能晋级。

## 5. 防守收益与错失上涨

以下是对每日已执行暴露的简单收益机会归因；它是加法诊断，不等于复合终值。

| 规则 | 防守收益 | 错失上涨 | 增量成本拖累 | 净 timing | benefit / missed |
|---|---:|---:|---:|---:|---:|
| 对称 RV21 | 3.7132 | 3.6448 | 0.0300 | +0.0385 | 1.019 |
| 非对称 re-entry | 1.0773 | 1.1402 | 0.0277 | -0.0905 | 0.945 |

价格出口将错失上涨降低 68.7%，但同时将防守收益降低约 71.0%。关键失败不是“进攻仍然太慢”，而是当前 SMA21 出口过早地把真正危险期也归类为恢复，防守收益下降得比机会成本更快。

## 6. 时间集中与危机

- 相对对称规则，17 个完整年度只有 35.3% 为正。
- 相对同平均暴露控制的正年度贡献集中度为 60.3%，高于 50% 门槛。
- 截止 2021-06-30 的 timing value 为 `-2.89%`，不是由最后半年单独造成。
- 删除 GFC 后同平均暴露 timing value 为 `-1.83%`；删除 COVID selloff 后为 `-17.98%`。
- COVID selloff 窗口的 active timing value 为 `+18.49%`，随后 COVID rebound 为 `-8.62%`；GFC 窗口为 `-1.01%`。

这说明规则在 COVID 这一次快速危机中有明显帮助，却没有跨危机稳定性；COVID 贡献反而掩盖了其余时期的负 timing。

## 7. 预注册判定

| 门 | 结果 | 主要失败项 |
|---|---|---|
| H1 重新进攻增量 | 失败 | 相对对称终值/CAGR为负；正年度仅35.3% |
| H2 非单纯加 beta | 失败 | 同平均暴露 -2.81%；net timing<0；benefit/missed<1 |
| H3 保留防守 | 失败 | 仅保留5.43%的对称 MDD 改善 |
| H4 稳健性 | 失败 | 年度集中60.3%；两个危机 leave-out 与中途终点均为负 |

总状态为 `completed_no_reentry_candidate`。`lockbox_authorized=false`、`mom255_transfer_authorized=false`。

## 8. 对下一步的含义

这一轮淘汰的是一个很具体的方法：**RV21 进入 + 两日站上 SMA21 就退出 + 单一高波 episode 内不再防守**。不能据此淘汰所有非对称状态机，也不能据此重新选择复杂模型。

它给出的更精确启示是：

1. 单纯“更快恢复满仓”并不等于正确识别进攻点；
2. re-entry 信号必须同时判断反弹是否可持续，而不只是价格刚刚转强；
3. 下一候选若存在，应直接预测/度量恢复后的上涨持续性和二次下跌风险，而不是继续扫均线长度；
4. 在提出新预注册前停止，不解封当前锁箱。

## 9. 精简证据

Git 只发布 `summary.csv`、`controls.csv`、`gate.json`、`config_resolved.toml` 与 `manifest.json`。逐日 NAV、control NAV 和 weekly states 保留在本地不可变 runtime bundle。
