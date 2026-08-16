# 本地实验运行区规范

本项目采用“代码仓库”和“实验运行区”分离的存储方式。目标是避免 OneDrive
同步与安全软件反复扫描大量 Parquet、日度 NAV、中间表和缓存，同时保持代码、
实验设计与精简结论可通过 Git 复现和审阅。

## 两个根目录的职责

代码仓库（可放在 OneDrive，并发布到 GitHub）只保存：

- `src/`、`scripts/`、`tests/` 与 `config/`；
- 研究设计、数据契约和实验报告；
- 小型人工审计账本；
- `metadata/frozen_dataset/` 中的数据哈希与门禁摘要；
- `results/published/` 中经过筛选的精简结果。

本地运行区保存：

```text
<runtime_root>/
  data/       # 冻结行情、PIT 股票池、来源快照和 QA 产物
  results/    # 完整实验 bundle、日度 NAV、交易和持仓
  cache/      # 可删除、可重建的供应商与因子缓存
  logs/       # 本机运行日志
```

不使用 junction 或软链接把大目录伪装回仓库。CLI 直接解析真正的数据根和结果根，
这样运行清单中的路径清晰，删除与备份边界也不会混淆。

## 本机配置

复制 `config/runtime.example.toml` 为 `config/runtime.local.toml`，填写本机绝对
路径。后者已被 Git 忽略，不能提交机器专属路径。当前也支持以下环境变量覆盖：

- `CROSSSECTION_RUNTIME_ROOT`
- `CROSSSECTION_DATA_ROOT`
- `CROSSSECTION_RESULTS_ROOT`
- `CROSSSECTION_CACHE_ROOT`
- `CROSSSECTION_LOG_ROOT`
- `CROSSSECTION_RUNTIME_CONFIG`

显式 CLI 参数 `--data-root`、`--output-root` 的优先级最高。检查解析结果：

```powershell
python -m momentum_reversal runtime-status --create
```

配置本地运行区后，`run-baseline` 与 `run-experiment` 默认从本地 `data/` 读取，
并向本地 `results/` 写入；G00 复现根和 G21 参考根也从同一个结果根派生。
Yahoo 缓存默认写入 `<runtime_root>/cache/yfinance`。

## 不可变与清理规则

1. 冻结数据版本禁止原地修改；任何数据变化必须发布新的 `dataset_version`。
2. 完整实验写入唯一 `run_id`；已完成 bundle 禁止覆盖。
3. `cache/` 可以随时删除重建；`logs/` 可按月归档或删除。
4. 完整结果只留本地，只有接受的摘要和报告才复制到 `results/published/`。
5. 迁移必须依次完成“复制 → 文件数/大小/SHA256 校验 → 数据门禁加载 →
   dry-run → 切换主运行根”。校验前不得删除旧副本。
6. 本地运行区是工作副本，不等于备份。删除 OneDrive 旧副本前，应另有外置硬盘
   或对象存储备份；至少保存冻结数据、manifest、QA 与有效实验 bundle。

## 安全软件与同步

若 McAfee 仍显著影响运行，可只排除可信的本地 `data/`、`results/`、`cache/`
与纯文本 `logs/`。不要排除源码目录、下载目录、`.venv` 或整个用户目录。排除项
由用户在安全软件界面设置，研究代码不会自动修改系统安全策略。
