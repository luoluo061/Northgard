# 给协助安装、试采和贡献数据的 Codex

当前为 **0.5.1-alpha 准备版**，面向普通单人验证和 **3v3 本人视角实验采集**。先读根目录 [AGENTS.md](../AGENTS.md)、[README.md](../README.md)、[游戏数据指南](GAME_DATA_GUIDE.zh-CN.md) 和 [3v3 说明](MULTIPLAYER_3V3.md)。没有后台上传服务，不需要修改或控制游戏。

## 1. 安装准备

确认 Windows x64、64 位 Python 3.11+。优先使用已有的 `python` 或 `py -3`；缺失时说明具体缺项，不静默安装其他运行环境。项目只有标准库依赖，无需 pip、管理员权限、防护关闭或 PowerShell 执行策略更改。

克隆到普通独立工作目录，不覆盖现有仓库、游戏和 captures：

```powershell
git clone https://github.com/luoluo061/Northgard.git
cd Northgard
python scripts/setup_local.py
python scripts/doctor.py
```

确认远端是用户指定的仓库并记录当前提交。公开仓库查看与克隆无需邀请或登录；贡献使用用户自己的 GitHub 账户，不索取令牌文本。

`setup_local.py` 从注册的 Steam 库定位安装，核验 `hlboot.dat` 和资源包中 `data.cdb` 的双指纹，再在采集器忽略目录生成最小规则。多个安装或定位失败时才使用：

```powershell
python scripts/setup_local.py --game-dir "D:\SteamLibrary\steamapps\common\Northgard"
python scripts/doctor.py --game-dir "D:\SteamLibrary\steamapps\common\Northgard"
```

替换成用户自己的安装目录。当前支持的游戏 build 与双指纹以 `research/runtime/build_profile.json` 为准。版本不匹配时保留诊断并停止读取，禁止改哈希强行兼容。`ready_for_local_test` 只表示环境准备好，不表示已经读到对局。

## 2. 选择本人视角与多人标签

每台电脑的采集对象都是本地 `state.me` 对应的人类玩家、自有单位和自有地块建筑。其他玩家的对象在内存里可访问，并不扩大导出范围。不要为了补齐六人信息读取名单、姓名、队友／敌方资源或隐藏地图。

普通单人FreeForAll另有可选的[已探索地块详情](KNOWN_ZONE_DETAILS.zh-CN.md)：仅增加经本局已探索与可见规则核验的白名单自然资源及无玩家归属的普通狼熊。它无需逐块点击；读取失败不影响既有本人组件。该新路径尚待真实短段验收，3v3明确返回`unsupported_context`，不能把单人的输出范围直接扩到多人。

单人验证直接进入用户已开好的普通熊／乌鸦单人局。3v3 组织者运行：

```powershell
python scripts/create_match_kit.py --output ..\northgard-3v3-kit
```

该命令只生成随机共享 UUID、`A1.metadata.json` 至 `B3.metadata.json`、`commands.txt`、`README.txt`，不会启动游戏、采集或上传。给愿意参与的玩家各自对应的模板与命令；只收本队三人也可以。不要替用户联系其他玩家，除非他们授权这样做。

同一真实对局使用同一个小写 UUIDv4，各人约定 A／B 和席位 1–3。它们是自报标签，与原生队伍序号和账号无关。本地 session、玩家实体 UID、单位 UID、地图 ID 不能跨电脑作为共同身份。每一局创建新包；输出目录已存在时不要覆盖。

注意 `commands.txt` 中的命令默认持续记录。首次短测需加 `--seconds 120`，并使用独立短测输出目录。

## 3. 用户正常玩，先做短测

已有“安装并采集这一局”的授权时按范围继续，不重复确认例行步骤。只有安装授权时先完成安装检查，等待用户开好并授权采集；不要替用户开局、选氏族、调岗或控制单位。

3v3 示例为 A1，需要替换共享 UUID：

```powershell
python collector/collect.py --seconds 120 --interval 0.5 --out-dir captures/3v3-short-A1 --shared-match-id "<本局参与采集者共用的UUID>" --declared-format 3v3 --team-label A --player-slot 1
```

单人短测：

```powershell
python collector/collect.py --seconds 120 --interval 0.5 --out-dir captures/singleplayer-short
```

未在对局、断线、进程关闭、读取竞争和字段不支持都应如实报告。先读 `manifest.json`、`latest.json` 和健康状态，再逐组件检查。一次启动或一帧粮木金并不说明整个采集链可用。

| 检查项 | 要报告的内容 |
|---|---|
| 身份与时间 | 本地人类玩家是否确认；session／run／segment；覆盖起止游戏秒；暂停时不累计时间 |
| 原生模式 | 是否读取到 `Multiplayer`／`Team3V3`；原生队伍结构、配置结构和一致性是否可读 |
| 玩家声明 | 共享 UUID、A／B、席位；与原生上下文分开，不用声明覆盖原生未知或冲突 |
| 本人经济 | 粮木金库存、缓存时钟、原生产消是否有效；缺口与竞态范围 |
| 职业与人口 | 各类本人单位是否可读；住房人口使用什么口径；其他氏族未知不能回退成单位总数 |
| 劳动与转岗 | 任务、指派、状态和加成输入覆盖；阶段区间是否保留；最终实产未知仍为 null |
| 增长与支付 | 市政厅真实进度和缓存输入是否可读；多人增长门槛／盛宴报价模型不适用时保持 null |
| 地图边界 | 仅允许的已知地图和自有实体；共享视野来源未区分，不把新已知地块全叫本人探索 |
| 基地一环／二环 | 检查固定市政厅参照、已知几何路径和本方单位／建筑引用；`partial` 表示完整覆盖未知，空环不等于没有地块 |
| 已知地块详情 | 普通单人检查资源类型、石铁原始余量/共享进度和限定狼熊计数；核对每地资源/野兽状态与草丛缺口；3v3应为`unsupported_context`，不能填零 |

队伍人数来自 `startPlayers` 槽位按当前存储 `teamLeader` 关系聚合，不是冻结的开局队伍历史。匿名槽数可能包括 AI，当前没有逐席位真人／AI 区分，不能称为“已确认六个人类”。数组／关系／时钟变化或布局不符须保留拒绝／unavailable 状态。同时读到原生 `Team3V3` 与人数一致，也只说明上下文检查通过，不代表真实 3v3 短测或完整局已验收。

## 4. 完整记录、停止和故障

向用户报告短测结果和未知项。按用户安排完整记录时省略 `--seconds`，使用新输出目录；不能把短测目录复用为整局。首次未采到开局的部分要保留缺失，不补造。

Ctrl+C、输出目录内的 `STOP` 空文件或已支持的游戏结束标志会触发停止并写最终 manifest。等待 `recording_state: "stopped"` 后再处理；`duration_limit`、`keyboard_interrupt`、`stop_file` 或连接失败不证明对局结束或胜负。不要强杀仍在写文件的进程后假称完整捕获。

游戏模拟秒是分析基准，`--seconds` 只是墙钟录制上限。零秒为 A800 年 3 月，每 60 游戏秒一月，月初／中／末各 20 秒为报告分组。读档、重连、回退和错误分段必须保留，不能跨段补产出、增长或转岗耗时。

若不能取得队友视角，继续保存本人有效数据；不从本机其他玩家对象补齐，也不把共享 UUID 视为已经完成跨机时钟同步。

## 5. 标签、经验和本地贡献包

使用本人席位模板，或复制 `schemas/metadata.multiplayer.example.json`。多人填写 `mode: "multiplayer"`，保留实际 `match_link`；未知氏族／地图／难度可为 null。目标、结果与经验水平按本人说明填写。`consent_to_share` 默认 false，生成模板不是分享同意。

反馈按 [PLAYER_FEEDBACK.md](PLAYER_FEEDBACK.md) 连接 A 年月／月段与游戏秒，区分实际观察、当时目的、解释假设、建议和反例。单位 UID 仅在这个贡献视角有效。不要写玩家姓名、Steam ID、聊天或联系方式。

录制停止后，依 [DATA_CONTRIBUTION.md](DATA_CONTRIBUTION.md) 准备本地脱敏包：

```powershell
python scripts/prepare_submission.py --capture captures/my-stopped-run --metadata my-match.json --output submissions-review
```

审阅 `SUMMARY.md`、`submission.json`、文件清单和压缩流，运行验证器。多人贡献即使原生模式已匹配，也必须标记 `experimental_multiplayer_unverified`；验证通过不升级稳定性。若 CLI 记录的标签和元数据非空值冲突，应查明哪份属于这局，不能随机覆盖或改哈希消除错误。

## 6. 按人类授权回传

本地安装、采集、生成包不等于授权上传到公开仓库。审阅完成后，已有“向此公开仓库贡献这一局”的明确授权则继续；缺少授权时再说明公开范围并询问，不能重复索取已给授权。使用 [贡献协议](DATA_CONTRIBUTION.md) 的 fork／独立分支／PR 流程。

不要上传原始 captures、research-raw、latest、PID、进程地址、绝对路径、存档、完整游戏定义、玩家名单或其他玩家经济。不要用其他云盘／聊天服务代替指定目标。数据 PR 仅含审阅数据和重建索引，代码或协议改进另开 PR。

最后向用户交代：安装所在目录和提交版本、短测／整局实际范围、各组件结果、仍缺的数据、文件保留位置、是否做了公开提交及链接。真实验收仍不足时直接说明，不把离线检查、贡献校验或可启动包装成已完成 3v3 实测。
