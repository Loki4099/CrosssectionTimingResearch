# R10A — RSP lockbox feature extension

唯一授权动作是从 Tiingo EOD 下载 RSP 2003-04-30 至 2026-06-30 的原始 JSON，使用既有适配器规范化 adjusted close，并与冻结的 R4A 2003–2021 RSP 快照逐日核对。重叠必须为4703行、日期完全一致、`tr_close`最大绝对误差不超过 `1e-10`。

输出仅允许原始响应、规范化日表、重叠审计和manifest。禁止读取G00 NAV、计算P00状态、生成策略targets、计算forward return或任何绩效字段。
