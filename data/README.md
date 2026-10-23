# 对局数据入口

`submissions/YYYY/UUID/`只接收`prepare_submission.py`生成、`validate_submission.py`验证且贡献者已授权分享的本人对局。当前目录没有历史玩家采集数据。

本仓库现已公开，任何人都可以直接clone或fork，无需访问邀请。上传到公开fork、PR或本目录的对局数据、标签与玩家经验会公开可见。元数据模板的`consent_to_share`仍默认`false`，须在本地脱敏和审阅后按贡献者授权改为`true`；已有这一局及本公开仓库的明确上传授权时不重复确认。

每局的`submission.json`保存采集/游戏版本、双指纹、来源、氏族、模式、地图条件、经验水平、目标、结果、标签与带游戏时间的反馈。`capture-manifest.json`与四条压缩流保存质量、分段和固定字段数据；SHA清单用于发现篡改。未知不补零，经验不当作已证机制。

0.4.0-alpha接收标记为`experimental_multiplayer_unverified`的多人本人视角实验数据。原生模式、Team3V3配置与起始队伍聚合人数分别保留；没有完整原生计数时不声称布局已核验。人数可能含AI，不代表六名人类。不会采集或上传队友／对手的单位名单、资源和隐蔽地图动态；多人整局实测仍待完成。

`catalog.json`是可复算的检索索引，不是分析结论：

```powershell
python scripts/validate_submission.py data/submissions --tree
python scripts/build_catalog.py --root data/submissions --output data/catalog.json
```

索引保留`matches`并增加`team_groups`：以可选的`match_link.shared_match_id`形成参与者自报候选组，只接收版本、双指纹和声明格式一致的贡献，已知队伍A/B与1–3号位置冲突会拒绝。每个人独立贡献、独立授权；共享UUID不是游戏原生的跨客户端身份，本地session、单位UID、原生队伍序号不能跨电脑拼接。元数据、采集manifest标签与submission中的共享标签不一致时拒绝，未知保持未知。旧单人v1贡献继续兼容。

增长、职业、资源、殖民、建造与经验都按游戏时钟连接。统一处理仍须按版本、模式、氏族、已学知识、季节、地块和质量状态分层，不能跨run/segment积分。

数据PR只包含自己这一局目录与重新生成的索引；代码/协议修改另开PR。保留本地脱敏、审阅、校验和PR审核流程，不要提交captures、研究原始文件、完整游戏定义或本机安装路径。公开代码不表示自动获得任何一局数据的上传授权，具体流程见[数据贡献说明](../docs/DATA_CONTRIBUTION.md)。
