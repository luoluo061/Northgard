# Northgard 本人对局采集与人口机制研究

当前优先做好[稳定记录与完整离线复盘](docs/RECORDING_AND_REVIEW.zh-CN.md)，实时战略辅助暂缓。停止后可用`scripts/summarize_capture.py`生成按session/run/segment分开的本地事实索引，再结合游戏机制做逐月复盘；它不会把截断记录当整局，也不会自动上传。

研究首先回答：**人口与资源怎样维持经济，各项投入会形成什么能力，不同氏族由此具备哪些发展可能。**在这些关系清楚后，再结合局面和玩家偏好选择目标、比较具体操作。

理解实战先读[氏族分工、攻略与解说资料](docs/STRATEGY_ROLES_AND_GUIDES.zh-CN.md)：结合单挑／团队赛制，理解清野、视野、发育转军、盛宴／餐食支援和骚扰怎样相互配合。人口公式用于解释这些游戏行为；当前库存和个人产量不能单独代表团队贡献。

具体研究见[熊、乌鸦与猞猁的战略因果案例](docs/CLAN_STRATEGY_CASES.zh-CN.md)：以真实文字路线反查当前知识前置、5铁15石预算、熊的恢复与区域布局、港口工作互斥、乌鸦转军加成、持续雇佣兵钱耗和猞猁盛宴状态。每项分别保留当前代码、条件演算、玩家经验及失败条件。

研究顺序现调整为：**统一口径 → 人口与资源运转 → 能力转换 → 氏族的发展路线 → 结合局面形成目标 → 比较操作**。当前资料从[机制资料入口与覆盖表](docs/MECHANICS_REFERENCE.zh-CN.md)阅读：包含[人口食耗与调岗](docs/POPULATION_ECONOMY.zh-CN.md)、[岗位基础渠道](docs/JOBS_REFERENCE.zh-CN.md)、[经济与能力转换](docs/ECONOMIC_CONVERSIONS.zh-CN.md)和[熊族知识树](docs/BEAR_KNOWLEDGE_REFERENCE.zh-CN.md)，并在`mechanics/catalog/`提供可复查的结构化卡片。基础配置、代码推导、条件计算与实局验证分别标记，未完成项不填零。

每层的后续工作见[具体研究任务](docs/MECHANICS_RESEARCH_PROGRAM.zh-CN.md)、[机制框架](docs/MECHANICS_FRAMEWORK.zh-CN.md)、[规则证据](docs/MECHANICS_EVIDENCE.md)与[验证计划](docs/MECHANICS_VALIDATION_PLAN.md)。[条件基线计算器](mechanics/README.md)已能计算普通熊／乌鸦同质人口的食物预算；它与采集器分开，不是完整模拟器或原生逐人实产。

从采集走向玩家指导的下一步见[运营助手设计草案](docs/OPERATIONS_ADVISOR_DESIGN.zh-CN.md)：联合视野、人口、殖民、建筑与能力期限，输出带预算、替代方案和信息缺口的行动卡。该设计尚未接入局内推荐，现有预算比较器不等于完整规划器。

[实时辅助参照研究](docs/REALTIME_ASSISTANT_REFERENCES.zh-CN.md)核查HDT/Firestone、WeakAuras、Spawning Tool、SC2 Planner、Bob's Buddy与CaptureAge等实际文档和选定源码，区分状态、规则提醒、流程、统计、模拟与复盘，明确本地即时计算和异步解释的实施顺序；不是这些功能已在本项目落地的声明。

新增范围与差距见[项目路线](docs/PROJECT_ROADMAP.zh-CN.md)和[地块对象扩展规格](docs/MAP_OBJECT_CAPTURE_SPEC.zh-CN.md)：包含中立生物/巢穴、遗迹宝藏、声望来源、中立势力和特殊地块。先从本机建立完整定义目录，再逐类实现可知状态、任务、奖励与资格；静态目录不等于本局已采到，也不以条目数宣称运行时完成度。

本项目通过只读本地游戏进程获取状态，无需截图或 OCR。当前为 **0.5.3-alpha 准备版**，包含**基地已探索一环／二环及本方用工、建筑的关联**，并保留 **3v3 本人视角实验采集与多人对局关联**。环层已有单人短段读取检查；完整窗口和真实3v3尚未验收。本版优先记录收尾与分局离线检查，保留既有Windows快照锁定修复。具体状态见 [验证记录](docs/VALIDATION_STATUS.md)。

新增[已探索地块详情](docs/KNOWN_ZONE_DETAILS.zh-CN.md)可选组件：普通单人FFA中，按本局已知地块和已审查显示规则读取自然资源类型、石铁矿余量／开采进度及限定狼熊计数，不要求逐块点选或截图。静态岗位／加成与本局实际产率分开，特殊状态保持未知；该组件已进入代码集成，真实短段与3v3验证状态单列，不因其他组件曾读到数据就认定它已实局通过。多人对局的既有本人采集继续可用，新地块详情组件明确标记不支持。

## 让自己的 Codex 安装

把[公开仓库链接](https://github.com/luoluo061/Northgard)和这段话发给自己的 Codex：

> 请先阅读 AGENTS.md、README.md、docs/INSTALL_WITH_CODEX.md 和 docs/MULTIPLAYER_3V3.md，帮我安装 Northgard 本地采集器。使用现有 Windows x64、64 位 Python 3.11+，校验我的游戏双指纹并生成本地必要规则。先完成检查；我进入本人玩的对局并通知后，再按我给的共享 UUID、队伍标签和席位做 3v3 短测，逐组件报告成功、未知和覆盖范围。不要操作游戏、读取敌方经济或上传原始研究文件。之后在我授权公开贡献这一局时，按文档生成带标签的本地审阅包，验证后提交到指定仓库。

查看和克隆无需邀请。安装与采集默认只在本机进行；提交到公开 fork、PR 或主仓库的数据和经验文字会公开可见。

**可直接转发给玩家：**我们在做 Northgard 人口与经济机制研究，想邀请熟练玩家用 [这个采集器](https://github.com/luoluo061/Northgard) 记录自己的一局，再补充当时为什么调岗、殖民或出兵。可以让自己的 Codex 按仓库说明安装；3v3 每人只采自己，只收本队也可以。目前是 0.5.3-alpha 试采准备版，真实3v3短测和整局尚未验收。数据先留在本机，审阅并同意后才公开贡献，经验和反例都欢迎。

## 本机安装

```powershell
git clone https://github.com/luoluo061/Northgard.git
cd Northgard
python scripts/setup_local.py
python scripts/doctor.py
```

只需要 Python 标准库，无需 pip 依赖或管理员权限。若 Steam 安装无法自动定位：

```powershell
python scripts/setup_local.py --game-dir "D:\SteamLibrary\steamapps\common\Northgard"
```

目录应是自己的实际安装位置。当前仅接受已核对的 `v4.3.29.43337 / Steam Build 25096976` 及对应 `hlboot.dat`、`data.cdb` 双指纹。游戏更新后不匹配就停止，不修改哈希绕过。`doctor` 的 `ready_for_local_test` 表示安装准备完成，不代表已经读到一局。

## 3v3 怎样开始

**一台电脑只采本地玩家本人。**想比较同局多个玩家，需要每位参与者分别安装、采集和决定是否贡献自己的数据；一人装好不会自动得到六个人的资源、操作与完整地图。

组织者生成一次协作包：

```powershell
python scripts/create_match_kit.py --output ..\northgard-3v3-kit
```

包内含本局参与采集者共用的随机 UUID、A／B 队伍各 1–3 号席位的元数据模板和命令说明。把对应席位发给愿意参加的玩家。只收本队三人的视角也可以，无需敌队安装。模板默认 `consent_to_share: false`；生成包不启动采集、不联系玩家、不上传数据。

本人已经进入 3v3 后，用自己的共享 UUID 和席位短测，例如队伍 A 的 1 号：

```powershell
python collector/collect.py --seconds 120 --interval 0.5 --out-dir captures/3v3-short-A1 --shared-match-id "<本局参与采集者共用的UUID>" --declared-format 3v3 --team-label A --player-slot 1
```

替换尖括号内容后运行；其他席位使用协作包的相应命令。`--seconds` 是墙钟测试上限，人口和经济分析始终用游戏秒。先看基础资源与身份，再逐项检查人口、劳动、地图、增长和支付组件；程序启动不代表全齐。完整记录时省略 `--seconds`，改用新的输出目录。

单人验证无需共享 UUID：

```powershell
python collector/collect.py --seconds 120 --interval 0.5 --out-dir captures/singleplayer-short
```

进入自己玩的熊／乌鸦普通单人局，依同一流程检查。玩家填写 `3v3` 不能把原生未知模式认证为 3v3。

停止方式：读到游戏终局后结束，或按 Ctrl+C，或在本次输出目录创建名为 `STOP` 的空文件。等待 `manifest.json` 写出 `recording_state: "stopped"` 后再打包。达到短测时限、手动停止和断线都不能直接视为整局结束。

六个视角的连接原则和检查步骤见 [3v3 采集说明](docs/MULTIPLAYER_3V3.md)。

## 能采到什么，哪些仍未知

| 数据 | 当前用途 | 必须保留的边界 |
|---|---|---|
| 本人库存与粮／木／金原生产消缓存 | 区分连续收支和一次性支付 | 非原子；全局产消不能直接分摊到每个人 |
| 本人单位 UID、职业／兵种、任务、指派与位置 | 连接农民→水手、木工→掷斧兵等同一单位变化 | 所有氏族的本人职业／类型可采；候选状态不等于实际入库 |
| 住房人口、上限与增长输入 | 解释劳力供给、住房与幸福 | 人口计数目前只核熊／乌鸦基础路径；其他氏族未知时不拿单位数替代 |
| 科技、工具、状态、本人建筑／地块加成输入 | 确定岗位收益适用条件 | 全部动态加成尚未闭合，未知保持 `null` |
| 指派→任务／位置→职业→生产候选 | 测转岗阶段和时间区间 | 不是完整点击日志，也不是纯走路时长 |
| 本人已知地图、自有建筑、殖民及支付状态 | 连起取得土地→施工→上岗 | 多人已知地图可能来自共享视野，来源未区分，不宣称本人亲自探索 |
| 单人已探索地块的自然资源、石铁余量与限定狼熊计数 | 连接资源机会、矿藏预算和清地信息 | 独立组件、按规则过滤；未覆盖／读取失败不补零，不证明完整资源表或殖民合法性 |
| 基地已知一环／二环、邻接路径和本方实体引用 | 连接扩张方向、施工占人、岗位与资源变化 | 只按已探索几何邻接分环；未探索、未分类不等于没有；资源点和可通行性尚未完整验证 |
| 原生模式和匿名队伍人数 | 记录 `Team3V3`、队伍结构等上下文 | 槽数可能含 AI；不导出名单、玩家名、队友／敌方经济 |

**逐人最终实产和逐人资源到账目前没有可直接读取的完整持久表**，相关字段明确为 `null`；这不等于工人零产出。市政厅进度可直接读，完整 `growthBonus` 未求值。单人增长门槛和盛宴报价模型不会套进 3v3；多人条件未验证的模型保持未知。

详细解释见 [游戏数据指南](docs/GAME_DATA_GUIDE.zh-CN.md)、[基地一环／二环](docs/BASE_NEIGHBORHOOD.md)、[逐人产出边界](docs/UNIT_PRODUCTION_LIMITS.md)、[增长与转岗](docs/GROWTH_AND_TRANSITIONS.md)。

## 数据回传与玩家经验

流程：**停止采集 → 填标签和经验 → 本地脱敏导出 → 验证并审阅 → 按已有公开贡献授权提交分支／PR → 统一处理**。没有后台自动上传。

- [贡献协议](docs/DATA_CONTRIBUTION.md)：字段、压缩校验、公开范围和 fork／PR 命令；多人包标记 `experimental_multiplayer_unverified`。
- [经验反馈](docs/PLAYER_FEEDBACK.md)：按 A800 年几月、月初／中／末和游戏秒，说明观察、目标、建议及反例。
- `match_link`：共用 UUID、声明赛制和 A／B 席位用于连接同局贡献。本地 session、单位 UID 和地图 ID 不能直接跨电脑合并。
- `data/submissions/`：只接收验证过的贡献包，不放整份 captures、原始研究流、存档或游戏资产。
- `scripts/build_catalog.py`：生成供统一分析的目录索引；各视角仍保留独立覆盖、缺口和质量标记。

不必凑齐六个人才可贡献；缺少的视角保留缺失，不能用本机能访问的其他玩家对象补齐。

## 开发与版本

```powershell
python scripts/run_tests.py
python collector/build_release.py --source --out-dir dist
```

游戏规则由安装流程从玩家自己的已核验文件生成，保存在忽略目录。仓库不发布游戏 PAK、EXE、完整字节码、翻译或美术资源。离线测试、安装检查和真实单人／3v3 验收分别记录，具体范围见 [VALIDATION_STATUS.md](docs/VALIDATION_STATUS.md)。
