# 人口增长与转岗：采集到什么，怎样用于理解一局游戏

本页对应采集器的 `population_growth` 和 `labor_transitions`。实现采用进程只读字段，不执行游戏 getter，不注入，不改写游戏。当前语义固定于验证过的 `hlboot.dat` 与 `data.cdb` 指纹；新补丁需要重新核对。本版功能通过离线合成测试，尚待新的人类单人局检验实际读取覆盖率。

## 用游戏时间解释“一个人的收益”

以 `GameState.time` 为时间坐标：一个游戏月为 60 游戏秒，一年为 720 游戏秒，普通开局为 A800 年 3 月。报告的“月初／月中／月末”分别是该月第 0–20、20–40、40–60 游戏秒的显示分组，并非游戏内部的三种阶段。暂停时墙钟继续走，游戏时间没有增加，不能把暂停计成生产、旅行或招募时间。

把一个现有人口从农田改派码头，可能先损失粮食、经过一段路途与转职、随后才增加克朗。建筑完成和工人到位之间也可能有空档。复盘应该分别记录这些阶段，不能把一次点击之后全体库存的变化都算成这名工人的贡献。

还要分清四个数量：

- 住房人口：原生人口计数规则的结果。普通军人一般仍占人口，特殊单位有例外。
- 单位对象数：地图上的本方单位对象数量，不等于住房人口。
- 可调劳力：当前适合转岗的人，尚需结合状态、路途、军队与计划判断。
- 正在产生已结算资源的人：必须有实际结算证据；一个生产标签、职业或移动动作都不足以单独证明。

熊族的 `BearMaiden` 是装甲熊，`Maiden` 是英雄，`AxeWielder` 是掷斧兵。转岗记录把这些类别分开，但不依靠名称推算住房人口，也不把英雄或装甲熊默认算成普通劳力。

## 为什么采集出生进度

一个人没出生，可能是住房满、幸福不足、禁用增长的规则，也可能只是进度还没攒够。只看人口曲线的平段无法区分这些原因，不能据此直接说“这里少生了两个人”。

本机普通熊／乌鸦增长链的静态核对结果是：

```text
普通招募门槛 = 55 + 5 × 当前住房人口 P
H > 0 时，增长速率 = sqrt(H) × 完整 growthBonus
H <= 0 时，普通增长速率为 0
```

`TownHall.updatePopulation` 按游戏时间差累加自己的 `growth`。到门槛不等于立刻出生：市政厅地块战斗及出生资格等路径仍可能延后；备用人口、特殊氏族与规则也需要单独处理。

### 直接读取的字段

| 输出 | 游戏含义 | 解读边界 |
|---|---|---|
| `native_inputs.resource_growth_progress` | `ResourcesComponent.growth` 的储存进度 | 与市政厅进度分开保存，不能混作唯一进度 |
| `town_halls[].growth_progress` | 每座自有市政厅的 `TownHall.growth` | 进度直接读出；同次读取进度改变会标记竞态并拒绝投影 |
| `native_inputs.current_happiness` | 生产更新使用的 `currentHappiness` 缓存 | 不是即时执行 `getHappiness()` 得到的完整重算值 |
| `native_inputs.population_cache` | 资源组件中的人口缓存 | 保留与父快照住房人口的一致性检查，不以单位对象数代替 |
| `native_inputs.population_cap_cache` | `nbMaxPop` 住房上限缓存 | 表示缓存观察，不能证明所有特殊氏族上限分支均已重建 |
| `native_inputs.growth_disabled_modifier` | 已核对的禁用增长标志位 | 是一个直接可读阻塞输入，不是完整 `getNoGrowthReason()` 结果 |
| `native_inputs.debug_population_can_grow` | 原生增长开关 | 未读取时为 `null`，不会默认开放 |
| `force_recruit`、`recruit_stack` | 资源与市政厅强制招募相关储存值 | 原值保存，不把栈变化单独解释成出生数 |
| `low_housing_timer`、`low_happiness_timer` | 缺住房／低幸福相关原生计时值 | 只记录值；未核对的计时逻辑不能用于推算损失 |

自有市政厅必须通过父快照、地块归属、建筑UID和本地人类身份复核。`TownHall.player` 是可空存储引用：非空时必须指向本人；为空时仍依赖已验证的本人地块归属，不能据此推成外人或零进度。读取期间引用变化会拒绝该组件。其他氏族玩家、市政厅的敌军数组、账户名与进程地址都不进入此组件。

### growthBonus 为什么仍可能是 null

`growthBonus` 是原生函数计算结果，不是一个已保存的同名浮点字段。它存在招募知识、其他知识、特定建筑、餐食、征服加成、特殊规则和氏族分支；不能因为“普通熊族看起来没有特殊效果”就填入 1。

本版记录正常已研究知识图中的 `Recruit`、`EndlessTide`、`PlotThrone` 是否出现，以及可观测的特殊知识来源、活动餐食、征服加成、附加规则存在信息。正常图里没有一项知识，不保证原生 `hasTech` 的所有特殊来源都没有提供它。这些输入可帮助后续机制核对，当前不代表全部加成已经展开。

`growth_bonus = null` 表示没有读取或完整重建这个原生计算。父快照的其他劳力／修正组件还会记录更多已核对输入，分析时可以关联；这里不会把未计算的值伪装成直接观测。

### 模型值与观察斜率分开保存

`model.next_recruit_threshold` 仅在父快照明确验证普通熊／乌鸦、单人 `FreeForAll`、非编辑器且无 Bifrost 数据时，给出已核对标准人口路径的静态公式结果，来源标明函数 `#13509`。模式缺失、特殊、未知或只剩去参数的简化名称，都不能默认是普通对局：门槛保持 `null`，并记录 `growth_threshold_mode_unverified`；原生进度仍可保存。`town_halls[].progress_fraction_model` 是储存进度除以这个模型门槛，可能超过 1，不做假定 0–1 的截断。其他氏族门槛未验证时同样保持 `null`。

`GrowthProgressTracker` 对同一录制段内相邻、有效、没有长缺口的两帧计算：

```text
观察增长率 = (后进度 − 前进度) / 游戏时间差
观察有效倍率估计 = 观察增长率 / sqrt(端点一致的缓存幸福)
到门槛有条件 ETA = (门槛 − 当前进度) / 观察增长率
```

有条件 ETA 的含义是：**如果刚观察的斜率继续保持，还要多久到达该门槛**，它不是原生 UI 倒计时，也不是实际下一次出生的保证。端点之间可能短暂变化，采样无法全部排除；市政厅受攻击与出生资格也仍未完整读取。

例如 P=10，模型门槛 105；在幸福缓存 H=4 不变的一段中，进度从 33 增至 37，游戏时间过去 2 秒，则观察增长率为 2，观察倍率估计为 1；如果此斜率保持，再过 34 游戏秒到门槛。这里的 1 是该短区间的观察推算，不能反称原生 getter 已返回 1。

以下情况不计算跨帧斜率：暂停导致时间差为零、游戏时间倒退、跨会话／录制运行／录制分段、读取错误、超过允许的游戏时间缺口、人口或幸福改变、已观察的增长加成输入改变、进度回落可能跨过出生／重置。增长率为零时不生成无限 ETA；已有明确阻塞输入时也不给到门槛 ETA。

## 转岗记录的阶段

`LaborTransitionTracker` 始终跟踪同一个单位 `uid`。首次见到一个 UID 只建立基线，不猜它是出生、招募还是恢复读取。已有单位的指派目标或指派任务改变时建立转岗候选，再关联后面的状态。

| 阶段 `phase` | 能说明什么 | 不能据此说明什么 |
|---|---|---|
| `assignment_changed_observed` | 两次采样之间指派目标或任务不同 | 精确点击时间、玩家意图、命令是否经过某个 UI |
| `assignment_cleared_observed` | 原目标引用不再存在于可投影状态 | 一定是玩家主动取消；也可能引用暂时不可验证 |
| `task_or_route_changed_observed` | 当前任务、路由引用发生变化 | 一定在走路或一定停止生产 |
| `location_changed_observed` | 位置或地块有变化 | 已经完成到岗；不能据此重建精确路径 |
| `occupation_changed_observed` | 同一 UID 的职业种类改变 | 相应资源已经实际入账 |
| `production_candidate_resumed_observed` | 新工作候选的生产标签及工作状态可观察 | 逐人实产开始、逐人最终率、库存已收到对应资源 |
| `tracking_incomplete` | 新指派、单位缺失、错误、缺口或录制结束阻断了继续关联 | 单位死亡、玩家取消、实际没有完成 |

`produce` 是候选生产资源，`enabled` 与 `has_no_work` 是状态输入。带回资源等移动行为仍可能属于工作过程，因此记录不会用 `curJob=Move` 直接断言产出为零。所有本模块事件都带有 `actual_resource_production_confirmed=false`。

### 为什么转岗时长是区间

假设在游戏时间 100 的快照中还在农田，101 的快照中已经改派码头，103 的快照中仍未见新候选产出，105 的快照中已经看到水手职业与新候选资源：

```text
指派变化窗口：(100, 101]
生产候选恢复窗口：(103, 105]
指派至候选恢复时长的保守界限：[2, 5] 游戏秒
```

这 2–5 秒可能包含命令处理、路途、进门、职业变化、采样延迟等，不能称“纯走路花了 2–5 秒”。如果随后总资源缓存才更新，缓存延迟还必须另记录，不能并入某个人的纯路途时间。

事件中的 `assignment_observed_window`、`milestone_observed_window` 保存原始采样端点，`elapsed_from_assignment_game_seconds_bounds` 保存上述界限。`assignment_sample_index` 和 `milestone_sample_index` 是 tracker 的调用序号，含错误帧，不冒充源文件物理行号；对照时还要使用同一 session／run／segment 身份。

改派另一个目标会结束上一段未确认的关联；单位没有再出现只记录“没有观察到，原因不明”。游戏时间回退、新读档或新录制段都重新建立基线。暂停中命令状态改变的窗口可以为零游戏秒，墙钟等待不会增加时长。

## 玩家经验怎样补充数据

贡献经验时，请给游戏历法与目标，而不只说某个职业“效率高”。建议包含：

1. A 哪年哪月、月内阶段，以及报告可定位的游戏秒。
2. 当时氏族、已知知识、季节、人口、住房、关键库存和正在准备的目标。
3. 实际把谁从哪里调到哪里，希望解决什么约束，例如冬前留粮、存木造房或攒克朗招兵。
4. 你认为更好的选择，以及它放弃的收益、需要的前置与风险。
5. 这是亲身经验、机制资料、根据本局数据的推断，还是希望验证的假设。

例如：“A802 年 7 月中，我把一名农民调为水手是为了下一次招兵。希望比较保留粮食缓冲和更早获得克朗的价值。”这样的意见能和真实 UID 转岗、库存、知识与后续行动对应；“熊族永远应该留五农”缺少适用条件，无法直接变成推荐器规则。

后续机制分析应先在同族、同季节、相近人口和相同已知加成中比较短窗口，分开生产、消耗、一次性支出和上限溢出。增长平段、职业变化、单位消失都应保留证据等级，缺字段时明确承认尚不能定论。

## 开发接线与验证

```python
raw["population_growth"] = capture_growth(
    h, me, state, owned_building_pointers,
    profile=profile, identity=identity)
projected["population_growth"] = project_growth(raw["population_growth"], raw)
projected["population_growth"]["observed_progress_intervals"] = growth_tracker.update(projected)
labor_events = labor_tracker.update(projected)
```

错误、拒绝或缺失帧仍需调用两个 tracker 的 `update`，让后续关联断开。录制结束时调用 `labor_tracker.finish()` 保存未完成段。`population_growth` 的源数据必须先经 allowlist 投影，不能直接把开发用原始进程快照放进分享包。

离线合成测试：

```powershell
python -X utf8 -m unittest research.telemetry.test_growth_capture companion.tests.test_growth_projection companion.tests.test_labor_transitions -v
```

这些测试核对字段边界、竞态、区间与重置行为，不替代新版本真实单人局验证。下一轮应实际检查市政厅进度变化、一次正常出生、住房满与解锁、幸福变化，以及一次农民转水手再返回的完整关联。
