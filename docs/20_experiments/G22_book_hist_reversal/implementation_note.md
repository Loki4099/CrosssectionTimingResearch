# G22 v2 实施说明：program 哈希勘误

状态：**已冻结；仅适用于 `g22-frozen-v3-v2`。**

G22 的预注册设计在结果产生前冻结了全部科学规则、参数预算、比较对象、判定门槛、会计与输出合同。设计正文把 `config/experiments/program.toml` 的 SHA256 误写为 `5d10ab208eec672f0258893391e3c58af402cab64834653570ccee12996a7bf9`；正式运行前的只读预检从实际冻结文件复算得到的正确 SHA256 为 `11394af02fa028abe4a11434874be31e33e692f55feb73e9236da9bf8d07d413`。

该差异是纯粹的 provenance 文本录入错误：实际 `program.toml`、G22 配置、设计中的公式、RV126/756 日状态、rev5/rev20 动作、72 条核心路径、成本/借券、G00 对照和成功门槛均未改变。实现从正式执行前起即对正确的实际 program 哈希 fail closed。

`g22-frozen-v3-v1` 在完成后的设计逐条审计中因上述设计文字与实际输入不一致而被判为治理无效；其目录不得改写、不得发布、不得进入经济结论或台账。v2 不读取 v1，不复用 v1 结果，必须从冻结数据和唯一 G00 reference 完整重跑。

v2 额外门禁：

- 运行 ID 必须为 `g22-frozen-v3-v2`；
- 原设计文件及其 SHA256 保持不变；
- 本实施说明、正确 program SHA、G22 配置、dataset/FROZEN 和 G00 manifest 全部写入 resolved config 与 manifest provenance；
- 除本条 provenance 勘误外，不允许改变原设计的任何科学或经济判定规则。
