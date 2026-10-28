# Northgard 本地采集器 0.5.3-alpha 准备版

面向单人验证和 **3v3 本人视角实验采集**。尚未完成真实 3v3 短测或完整局验收，不宣称联机稳定。先看[仓库 README](../README.md)、[Codex 安装流程](../docs/INSTALL_WITH_CODEX.md)和[3v3 说明](../docs/MULTIPLAYER_3V3.md)。

一台电脑只记录本人资源、本人单位职业／兵种／任务、自有建筑与允许的已知地图；不导出名单或其他玩家经济。各氏族本人职业可采，不等于各氏族住房人口与经济模型全齐。逐人最终率、完整动态加成和精确出生时间仍有未知项；单人增长／盛宴报价模型不套多人。

0.5增加已探索的[基地一环／二环](../docs/BASE_NEIGHBORHOOD.md)：以固定本方市政厅参照，用地块邻接划分，并连接本帧本方单位和建筑。尚未探索或不能分类的部分保留未知，资源点与通路未完整验证；已有单人短段读取，完整录制仍待验收。

普通单人FreeForAll增加独立的[已知地块详情](../docs/KNOWN_ZONE_DETAILS.zh-CN.md)：开图后后台读取受支持的自然资源、石铁余量/开采进度和无玩家归属的普通狼熊计数。默认资源属性与实际产能分开；草丛和特殊状态保留未知。组件已接入正式采集链，新路径尚未真实短测；多人返回`unsupported_context`，不影响原有本人视角采集。

0.5.1修复本地快照读锁：`latest.json`或周期manifest刷新失败会有限重试并标记`local_publication_status: degraded`，不把它当成游戏读取失败，也不中断已在追加的NDJSON流。最新快照可能暂时落后，应同时看追加流与健康状态。初始或最终manifest不能发布时仍明确失败，不把未完成元数据的目录当作已验收贡献包。

```powershell
python scripts/setup_local.py
python scripts/doctor.py
```

在仓库根目录运行。用户正常进入对局并授权采集后，用新目录短测；3v3 共享 UUID、A／B 和席位参数见多人说明。首次可用 `--seconds 120 --interval 0.5`；完整记录省略 `--seconds`。暂停不计产出时长。读到支持的结束标志、Ctrl+C 或输出目录 `STOP` 空文件会停止；等待最终 manifest 后再导出。

采集默认仅保存在本机，无自动上传。多人协作可运行 `scripts/create_match_kit.py --output <新目录>` 生成六个席位的模板和命令，只收本队也可以；每人各自安装、采集和授权。贡献经过本地脱敏、标签、经验反馈与验证，再按[贡献协议](../docs/DATA_CONTRIBUTION.md)回传。多人包始终标记 `experimental_multiplayer_unverified`。

源代码包不含游戏资产或历史对局；`research/static` 从玩家自己的已核验安装生成且不入库。实际验证范围见[VALIDATION_STATUS](../docs/VALIDATION_STATUS.md)。
