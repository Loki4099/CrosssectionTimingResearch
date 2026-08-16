# 本地运行区迁移状态

更新日期：2026-08-16
状态：**迁移、验收、旧路径清理和隔离区永久删除均已完成；九宫格主网格已在本地运行区完成。**

目标运行区：

`%USERPROFILE%\QuantWork\MomentumRversionMethod-runtime`

已永久删除的隔离区（审计路径）：

`%USERPROFILE%\QuantWork\MomentumRversionMethod-old-onedrive-quarantine-20260814`

## 已完成

- 本机 `config/runtime.local.toml` 指向目标运行区且不进入 Git。
- CLI 的数据、结果、G00 复现根、G00 对照根、缓存和日志均从本地运行区派生。
- 运行区写权限验证通过。
- `data/` 共 208 个文件、575,267,766 bytes；与旧 OneDrive 副本逐相对路径、大小和 SHA256 全等。
- `results/g00-long-only-frozen-v3/` 共 525 个文件、214,121,214 bytes；逐文件 SHA256 全等。
- `results/experiments/G00/` 与 `G21/` 各 9 个文件，分别为 71,440,753 和 117,251,934 bytes；逐文件 SHA256 全等。
- `runtime-status`、冻结数据完整加载门禁和 G21 dry-run 均通过。冻结数据加载 2,527,785 行价格、2,134 个评价交易日，manifest SHA256 为 `65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7`。
- OneDrive 仓库中的 `data/`、`results/archive/`、`results/g00-long-only-frozen-v3/` 和 `results/experiments/` 已移出；`results/published/`、`metadata/frozen_dataset/`、`input/data_repair_v3/` 保持原位。
- G31 已在本地运行区实现并完成 `g31-frozen-v3-v1`：36 条核心策略、288 个场景、1,440 行 G00 比较，完整大型结果未回写 OneDrive。bundle 验收 0 项失败。
- G32 已在本地运行区完成 `g32-frozen-v3-v1`：36 条核心策略、288 个场景、1,440 行比较，完整大型结果未回写 OneDrive。
- G33 已在本地运行区完成 `g33-frozen-v3-v1`：36 条核心策略、288 个场景、1,440 行 G00 比较，bundle 验收 0 项失败，完整大型结果未回写 OneDrive。
- G11 已在本地运行区完成 `g11-frozen-v3-v1`：36 条核心策略、288 个场景、1,440 行 G00 比较，bundle 验收 0 项失败，完整大型结果未回写 OneDrive。
- G12 已在本地运行区完成 `g12-frozen-v3-v2`：36 条核心策略、288 个场景、1,440 行 G00 比较，bundle 验收 0 项失败，完整大型结果未回写 OneDrive；v1 在 bundle 创建前由审计器 fail closed，未留下目录。
- G13 已在本地运行区完成 `g13-frozen-v3-v2`：36 条核心策略、288 个场景、1,440 行 G00 比较，bundle 验收 0 项失败，完整大型结果未回写 OneDrive；v1 在 bundle 创建前因缺少派生审计列 fail closed，未留下目录。
- G22 已在本地运行区完成有效运行 `g22-frozen-v3-v2`：72 条核心策略、576 个场景、2,880 行 G00 比较，bundle 验收 0 项失败，完整大型结果未回写 OneDrive。v1 因设计正文 program SHA 录入错误保留为本地未发布治理无效证据；v2 不读取 v1 并从冻结输入完整重跑，七个科学结果文件与 v1 逐字节一致。
- G23 已在本地运行区完成 `g23-frozen-v3-v1`：72 条核心策略、576 个场景、2,880 行 G00 比较，bundle 验收 0 项失败，完整大型结果未回写 OneDrive。

## 清理方式与恢复边界

旧路径共移出 4,402 个文件、1,969,913,333 bytes；四个旧目录完整移动到上述隔离区时，文件数与字节数复核一致，OneDrive 旧路径已不存在。

2026-08-15，在用户明确授权永久删除后，先复核隔离区的绝对目标路径及父目录，再对该精确路径执行递归永久删除。删除后再次检查，`%USERPROFILE%\QuantWork\MomentumRversionMethod-old-onedrive-quarantine-20260814` 已不存在。隔离区内原 4,402 个文件、1,969,913,333 bytes 不能从本项目恢复；OneDrive 上的四个旧路径也继续保持不存在。

## 当前运行锚点

- G00：`results/experiments/G00/runs/g00-frozen-v3-v1`，manifest SHA256 `8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66`。
- G21：`results/experiments/G21/runs/g21-frozen-v3-v1`。
- G31：`results/experiments/G31/runs/g31-frozen-v3-v1`，完整树 SHA256 `f9079b2aaff1640d874f6dba762055e1ce2037ba24c7523d6735a7bc4b9f393a`。
- G31 结论：long-only 最大回撤 18/18 改善，但只有 2/18 同时改善 Sharpe 与回撤，预注册 H1 失败；long-short 机制增量为正但绝对表现弱。
- G32：`results/experiments/G32/runs/g32-frozen-v3-v1`，manifest SHA256 `0d85979a75ed02d9033fa3977e06585483e5ca9e3235049d23ac0bdfc5b1cb2d`，完整树 SHA256 `64c2ec8c2f7c3dd4b8b60786ea16e22dcd695fe762a7b21284c4d59865a94c3d`。
- G32 结论：long-only H1 以 0/18 失败，CAGR 与 Sharpe 18/18 下降、最大回撤改善结果混合；long-short 在 17/18 个主场景同时改善 Sharpe 与最大回撤，成本/借券压力下机制稳健，但绝对表现仍弱。
- G33：`results/experiments/G33/runs/g33-frozen-v3-v1`，manifest SHA256 `11803b8b934a768075f2aae7ca6830e18848c960775d95db74b3636a91b50259`，完整树 SHA256 `98b01d10c4edd9d0faf2ed8f7f835175ff8fe642ba6c31ded4eaaefc3b8a4c6f`；`formal_run_eligible=false`。
- G33 结论：long-only H1 以 0/18 失败，CAGR 与 Sharpe 18/18 下降、最大回撤 18/18 改善；long-short 回撤改善稳健，但仅 10/18 个主场景同时改善 Sharpe 与最大回撤，Sharpe 改善对成本/借券费敏感且绝对表现仍弱。
- G11：`results/experiments/G11/runs/g11-frozen-v3-v1`，manifest SHA256 `0f194b071d4c11af0e85de8acc61343f03f4ac341496909eee4507bc1be7eb1a`，完整树 SHA256 `22eac8960c2c4a8516f2b0cdf12fea653ff7e81e981f59635ea10a1a23981cb5`；`formal_run_eligible=false`。
- G11 结论：long-only H1 以 0/18 失败，CAGR 与 Sharpe 18/18 下降、最大回撤 18/18 改善；long-short 在 18/18 个主场景及全部 216 个成本/借券压力场景中同时改善 CAGR、Sharpe 与最大回撤，但绝对表现仍弱。
- G12：`results/experiments/G12/runs/g12-frozen-v3-v2`，manifest SHA256 `db80b885245427ba2684dddcd267ddecb029f2daeeaa6b68fd746e5dd8ccf5e1`，完整树 SHA256 `a3d5708d143d79c3ab51d0472c05f3f16398364e98a796d679630b7e07867dce`；`formal_run_eligible=false`。
- G12 结论：LO H1 以 0/18 失败，CAGR/Sharpe 18/18 下降、MDD 16/18 改善，连续 RV126 规则过度保险；LS 在 17/18 主场景和 204/216 压力场景同时改善 Sharpe/MDD，但绝对表现仍弱。
- G13：`results/experiments/G13/runs/g13-frozen-v3-v2`，manifest SHA256 `d0adf3b7861693b51912426ba4ebb87b02be342f7c62959449abeaeb85d808c7`，完整树 SHA256 `5b4ec84726ad8acab886758664e21b51440eb43f223da85eb266341db4d5203d`；`formal_run_eligible=false`。
- G13 结论：LO H1 以 0/18 失败，CAGR/Sharpe 18/18 下降、MDD 18/18 改善，连续 EWMA 预测目标过度保险；LS 在 12/18 主场景和 132/216 压力场景同时改善 Sharpe/MDD，但成本/借券敏感且绝对表现弱。
- G22：`results/experiments/G22/runs/g22-frozen-v3-v2`，manifest SHA256 `c1189bad71c20c2583c6a692decccfee60d535588735ec8952b098575749ee0a`，完整树 SHA256 `534ff33b43693d7adce4a4a5beeb3d600f26fb0091efe43c5e9903b07b2298ae`；`formal_run_eligible=false`。
- G22 结论：LO 仅 4/36 联合改善且 MDD 中位恶化，H1 失败；LS 为 23/36、月频 8/18，未过平台门槛，周频为成本敏感且绝对表现弱的局部机制。
- G23：`results/experiments/G23/runs/g23-frozen-v3-v1`，manifest SHA256 `c0f90d3e864ce29a5f7e7f3a7ea94a26083680749e6b83b1e427138f05d5767f`，完整树 SHA256 `96c8608f498b7fb68e5192609d7163a22748e428bd18cca71073dc741c357c11`；`formal_run_eligible=false`。
- G23 结论：LO 0/36 联合改善，Sharpe 与 MDD 无一改善；LS 33/36、月频 15/18、周频 18/18 通过平台门槛，但最高成本/借券压力敏感且绝对表现弱。

G11–G13、G21–G23、G31–G33 九宫格主网格已全部完成。XS01 属于九宫格外补充实验，未在本轮自动启动。大型本地结果不进入 Git；代码、报告与精简证据通过独立分支和 PR 发布。
