# 贡献协议 v1

这些 JSON Schema 文件由 `python scripts/export_submission_schemas.py` 从 `scripts/submission_contract.py` 生成，供人和其他工具检查字段。不要只编辑某份 JSON 副本；实际标准库校验器使用同一份 Python 契约，CI 从可信 base 分支加载它。

- `metadata.example.json`：贡献者填写的标签、同意分享、环境、目标、结果与经验。
- `metadata-v1.schema.json`：元数据字段约束；反馈的时间锚点还必须标记 `time_precision`。
- `submission-v1.schema.json`：每局 UUID、采集版本、游戏 build／fingerprint、来源身份摘要、覆盖范围与文件摘要。
- `capture-manifest-v1.schema.json`：重新生成的最小停止状态清单，不复制原本机 manifest 的路径或自由字段。
- `record-*-v1.schema.json`：四个压缩 NDJSON 流的逐条固定字段树；未定义字段在验证阶段拒绝。

游戏运行中的采集 JSON 和入库 JSON 不是完全同一格式。导出会保留已定义的本人单位、经济缓存、已探索静态地图、殖民／建筑成本、劳动输入、增长进度和转岗阶段，并移除 raw、进程信息、未定义字段、墙钟采样时间、原生任意事件参数，以及可从已保留源状态重新计算的 ledger／situation 包装。

新增模块中 `native_actual_rate`、`native_settled_amount`、`model_effective_rate`、`growth_bonus` 目前必须是 `null`，不能填静态基础值冒充实产／完整加成。原生输入、候选配置、采样差分／斜率估计仍分别保留；详见 [游戏数据解释](../docs/GAME_DATA_GUIDE.zh-CN.md)、[增长与转岗](../docs/GROWTH_AND_TRANSITIONS.md) 和 [贡献流程](../docs/DATA_CONTRIBUTION.md)。

JSON Schema 之外还验证 SHA-256、gzip／ZIP 限额、禁止路径与常见凭证、本人对象范围、已探索地图边界、游戏历法、多个 session、覆盖边界、目录名称及重复 capture/session。通过协议只说明数据结构与来源声明可审阅，不能证明贡献者没有造假，也不能证明相关性具有因果关系。
