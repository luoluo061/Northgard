# 实时辅助参照：现有游戏工具怎样取数、提示与规划

研究日期：2026-10-28。查阅项目官方文档、作者仓库和选定源码；没有安装、编译、运行这些工具，也没有验证其在当前游戏发行版上的全部功能。本文提供实现参照和本项目设计决策，不宣称Northgard已经有完整实时建议引擎。现有采集范围仍见[验证状态](VALIDATION_STATUS.md)，总体目标见[项目路线](PROJECT_ROADMAP.zh-CN.md)。

## 1. 先区分实际能力

| 能力 | 解决的问题 | 不能据此推导 |
|---|---|---|
| 状态呈现 | 正在发生什么、有哪些可知内容 | 已经知道下一步最优动作 |
| 规则提醒 | 某个可检查条件是否成立 | 已比较完整运营路线 |
| 流程指导 | 跟随预先选择的步骤和计时 | 能识别实际完成或自主换路线 |
| 条件统计 | 类似局面中常见选择和结果 | 统计相关就是此动作的因果收益 |
| 有限模拟 | 在已知规则和输入下比较结果 | 包含未知地图、对手反应和所有机制 |
| 自适应规划 | 用当前状态选择并更新可行路径 | 一次生成全年计划后永远正确 |
| 复盘 | 回看投入、过程和结果 | 过去的完整信息当时就已可知 |

实时显示和实时决策是两种能力。在本轮所查项目中，没有找到可直接搬到Northgard、无需游戏规则模型就能提供完整运营最优解的现成插件。

## 2. 已检查的具体参照

### Hearthstone Deck Tracker：事件维护状态，显示与建议分层

HDT的已检查路径为日志监听、按namespace分发、实体/tag与游戏状态更新、overlay显示。当前源码还包含HearthMirror和IPC相关补充读取，不能概括为纯日志工具。显示层处理游戏窗口位置、前后台和可见性，不要求每次请求聊天模型。

可复核入口固定到本次检查的提交`51000834bbba42e90f877d23387fb88c43fca9ae`：[LogWatcherManager](https://github.com/HearthSim/Hearthstone-Deck-Tracker/blob/51000834bbba42e90f877d23387fb88c43fca9ae/Hearthstone%20Deck%20Tracker/LogReader/LogWatcherManager.cs)、[PowerHandler](https://github.com/HearthSim/Hearthstone-Deck-Tracker/blob/51000834bbba42e90f877d23387fb88c43fca9ae/Hearthstone%20Deck%20Tracker/LogReader/Handlers/PowerHandler.cs)、[Core](https://github.com/HearthSim/Hearthstone-Deck-Tracker/blob/51000834bbba42e90f877d23387fb88c43fca9ae/Hearthstone%20Deck%20Tracker/Core.cs)。

当前留牌模块有上下文统计、提示与理由，并区分LOW_DATA、NO_DATA；不能说HDT只有记牌没有建议。这些功能也不证明整局最优行动搜索。参照[留牌模块源码](https://github.com/HearthSim/Hearthstone-Deck-Tracker/blob/51000834bbba42e90f877d23387fb88c43fca9ae/Hearthstone%20Deck%20Tracker/Controls/Overlay/Constructed/Mulligan/V2/ConstructedMulliganV2SingleCardHeaderViewModel.cs)。

对Northgard的启发：采集、状态归并、提示模块、显示分别维护；对稀疏经验主动显示样本不足。炉石实体协议、内存补充入口和统计模型都不能直接移植。

### Firestone：把不同来源送入同一状态队列

已检查的[GameEventsPluginService](https://github.com/Zero-to-Heroes/firestone/blob/0a30f066eb77a417637d95cec86444456f607ba1/libs/game-state/src/lib/logs/game-events-plugin.service.ts)通过Overwolf的转换组件将日志变成JSON游戏事件；[GameStateService](https://github.com/Zero-to-Heroes/firestone/blob/0a30f066eb77a417637d95cec86444456f607ba1/libs/game-state/src/lib/services/game-state.service.ts)将事件和内存补充信息送入同一处理队列。源码说明了避免并发修改局面的目的。上述是公开提交快照，不保证等于当前打包发行版。

启发：不同组件的到达先后不能制造两份相互矛盾的当前状态。Northgard需要串行归并和身份/时间核验；这不等于把非原子的采样变成原子快照。

### WeakAuras：可组合、可解除的即时提示

官方文档区分Status与Event：Status可在初始化时检查已有状态；Event使用事件参数，并通过计时或untrigger解除显示。TSU进一步以状态表驱动多个显示。依据：[Custom Triggers](https://github.com/WeakAuras/WeakAuras2/wiki/Custom-Triggers)、[TSU](https://github.com/WeakAuras/WeakAuras2/wiki/Trigger-State-Updater-(TSU))与[GenericTrigger](https://github.com/WeakAuras/WeakAuras2/blob/main/WeakAuras/GenericTrigger.lua)。这里借鉴已有事件架构，不认证它在各个WoW版本上的兼容性。

启发：粮食危险、住房接近上限、计划材料不足可以各自成为小规则；启动时就检查，不必等下一次变化。问题解决或证据失效时撤销卡片，同一问题更新原卡，不反复弹窗。

### Dota2GSI开源库：区分状态差分与游戏原生事件

作者库接收游戏HTTP状态，维护GameState，并从前后快照生成细粒度通知。游戏提供的events另行去重处理。依据：[项目与配置](https://github.com/antonpup/Dota2GSI)、[GameStateListener](https://github.com/antonpup/Dota2GSI/blob/master/Dota2GSI/GameStateListener.cs)、[GameStateHandler](https://github.com/antonpup/Dota2GSI/blob/master/Dota2GSI/GameStateHandler.cs)、[GameplayEventsHandler](https://github.com/antonpup/Dota2GSI/blob/master/Dota2GSI/StateHandlers/GameplayEventsHandler.cs)。该库是作者项目，不是Valve为Northgard提供的接口。

启发：原生事件、快照差分、策略推断分开保存。已检查的HeroTookDamage分支只检查前后血量变化且都存活，没有显式要求下降，说明第三方事件名称本身也不证明语义；需保留前后证据并检查边界。[对应源码](https://github.com/antonpup/Dota2GSI/blob/master/Dota2GSI/StateHandlers/HeroHandler.cs)。

### Spawning Tool：可用的局内流程提醒，不足以证明自适应

[作者官方说明](https://lotv.spawningtool.com/overwolf/)支持先选择build，在SC2 ladder/custom中叠加步骤，并在开局同步游戏计时。未取得可审查的Advisor实现，也没有文档证据证明它持续核实建筑完成、处理暂停或按资源/敌情自动改路线。

已查的[Overwolf SC2事件文档](https://dev.overwolf.com/ow-native/live-game-data-gep/supported-games/starcraft-2)列match_start/match_end；不能由此推断资源等全部状态可用，也不能反推Advisor一定使用此入口。

启发：熟练玩家路线可以成为带条件的教学模板，但应由实际状态决定是否推进。Northgard不能只照A800某月播放下一条口令；中断、缺前置和新发现需要重新判断。

### SC2 Planner：规则模型与少量候选重排

[作者仓库](https://github.com/BurnySc2/sc2-planner)描述的是编辑build并模拟动作可执行时间的网页；Live Demo指在线网页，不表示连接真实对局。已查[gamelogic.ts](https://github.com/BurnySc2/sc2-planner/blob/master/src/game_logic/gamelogic.ts)推进资源、任务和动作前置；[optimize.ts](https://github.com/BurnySc2/sc2-planner/blob/master/src/game_logic/optimize.ts)尝试补工人/人口及重排步骤，并重新模拟比较。内部模拟帧不是现实计时器；源码不能证明全局最优或实局自动重规划。

启发：用有限模拟解释某条计划卡在什么条件。Northgard可先比较少量调岗/建设候选到同一期限形成的粮木金、人口与能力，不应先枚举整个游戏。

### Bob's Buddy：边界明确的规则模拟

[官方介绍](https://articles.hsreplay.net/2020/04/24/introducing-bobs-buddy/)说明按酒馆战棋规则重复模拟战斗，显示胜平负等概率，并将预测与实际战斗核对；未知信息揭示后可重新计算。它是针对局部战斗问题的模拟器，不是让语言模型每次重新理解全部规则，也不能证明Northgard的完整经营或战斗已经可模拟。文章里的速度与次数描述不作为本项目性能指标。

启发：逐个做有适用域的小模型，先校准，再组合。Northgard初期更适合先模拟住房、材料支付和限定生产窗口；完整战斗、寻路与对手应对留在各自未完成的范围。

### CaptureAge：复盘表达与独立时间轴

[Overview](https://captureage.com/cade/docs/cade-latest/overview)将其定义为AoE2 DE外部观战客户端；自己的live gameplay需大厅启用cheats。不能称其为普通玩家对局中自动给建议的教练。可借鉴的是[时间轴控制](https://captureage.com/cade/docs/cade-latest/time-controls)和[独立记录播放](https://captureage.com/cade/docs/cade-latest/caderecs)，底层接入算法未公开核实。

启发：事后回到建议时刻，看当时可知状态、计划、人员安排与后续结果；不拿后来的全图和实际结果倒填过去。

## 3. Northgard应采用的组合

以下是待实现设计，当前只有既有采集器及部分离线预算/机制模块，不能称整条链已运行：

```text
已核只读采集 → 带质量与时钟的状态归并 → 相关变化事件
                         ├→ 即时规则 → 提醒状态 → 本地面板
                         └→ 少量可行候选 → 限定模拟 → 建议卡
                                                           └→ 异步解释
全部原始观察、派生结果和反馈 → 本地日志 → 回放与误报检查
```

1. **保留现有来源身份。** session_key/capture_run_id/capture_segment_id、sequence、构建与规则版本、采集时间和游戏时间继续存在。加入内部数据契约时不能打散它们或用新名称绕过既有校验。
2. **先用可信汇总做快速提醒。** 食物stock/net/cache age等已核输入可支持有前提的短期耗尽检查；不必等逐人最终收入台账。原生缓存、外层库存时点和数据质量须兼容，不能拼成原子账本。
3. **按依赖触发。** 人口/容量/施工变化重算住房；资源/费用/计划变化重算付款；发现地块/清地/归属变化更新殖民候选。首次快照只建立基线，不能制造从零新增的事件。
4. **有限规划。** 模板提供符合氏族和局面的少数候选；分别核可知信息、资格、材料、有效人手和期限，输入不足时解释缺项。短期模拟按出生、季节、支付、完成等事件分段，保留各币种，不统一折成永久分数。
5. **解释异步。** 快规则和状态显示不等待网络或大模型；模型读取结构化结论及证据，解释复杂取舍。异步结果回来后重新检查依赖与适用条件；局面已改变则废弃或重算。
6. **显示与读取解耦。** 先用本地浏览器面板/SSE即可验证交互，之后再选择独立悬浮窗；不为做overlay引入新的游戏读取或自动操作。UI可合并中间状态刷新，但重要离散事件和提示日志不能丢。

## 4. 提醒要有生命周期

每条卡保留rule_id/version、同局对象键、相关观察引用、游戏时间、触发原因、缺项、当前严重程度和解除条件。状态可为inactive/pending/active/resolved/unknown，用户可暂缓；未知不等于已解决。

同一问题更新同一张卡；边界抖动通过进入/退出阈值和持续条件控制。冷却仅限制重复声音或弹窗，不应延迟解除、风险升级或数据失效。详细阈值需在回放和实局中调整，不能照抄其他游戏数值。

暂停和数据断流须分开：新鲜采样但游戏时钟不动，是时间停滞的证据，未必已证明某个暂停按钮状态；样本墙钟变旧则是采集可能失效。游戏时间不动时，不减少预测剩余游戏秒；链路健康仍用本地单调时钟。换局、回退和读档重建状态，不继承上一局提醒。

异步建议不因每次sequence递增就全部无效，否则永远追不上；要检查其依赖的计划、岗位、费用与地图条件，并以当前状态复核预算。真正失效的建议不能留在屏幕上让玩家执行。

## 5. 从提醒到建议，验收应分别进行

| 阶段 | 最小产物 | 验收重点 |
|---|---|---|
| A：状态与快提示 | 常驻状态、低粮、住房容量、已声明计划的材料缺口 | 出现及时、条件准确、解除及时、暂停/断流/换局正确、无重复骚扰 |
| B：计划跟踪 | 熊族阶段模板、前置/完成/跳过条件、偏离说明 | 实际完成后推进；超时本身不算失败或已完成；地图与职责改变可重选 |
| C：少量候选比较 | 调岗、补房、木场/采矿等条件预算 | 同起点同期限、合法性、施工与机会成本、未知隔离、预测与后续观察核对 |
| D：经验与解释 | 熟练玩家条件案例、异步解释、选择反馈 | 样本稀疏、适用域与版本；统计不冒充因果；解释不阻塞即时提醒 |

建议质量与性能分别测量：记录采样、计算、入队/完成、送达和实际显示时刻，报告端到端及各段p50/p95、过期卡比例；另计误报、漏报、重复提醒、解除延迟与缺项拒绝。每阶段先定测试窗口与可接受误差再测，不把某个轮询周期或单帧读取时长当端到端承诺。

反馈应保存建议版本、触发快照、实际显示时间以及采纳/暂缓/另有计划的声明。后续采集到相似动作，不自动证明玩家采纳，也不证明建议导致结果变好。完整本地回放是第一批验证数据；公开贡献继续按原有审阅与该局授权流程。

## 6. 本轮对实施优先级的结论

先交付可持续刷新、会撤销过期内容、能本地回放的辅助外壳和少量可信规则，再补模板与限定模拟。无需等待全游戏解码，也不把缺失机制补成零来凑建议。地图扩展、人口/知识/费用模型继续按[对象规格](MAP_OBJECT_CAPTURE_SPEC.zh-CN.md)与已有机制任务补齐。

参考这些项目的分层与验证方式，不复制它们的游戏协议、全知观战范围、经济参数或自动执行层。本轮未安装新依赖、未修改游戏、未扩大采集范围；没有为新建议系统报告尚不存在的实测延迟或成功率。
