# 先理解规则，再使用采集数据

本目录是与采集器分开的**条件基线模型**。它不访问游戏、不操作人口、不输出原生逐人实产，也不自动推荐岗位。研究框架见 [MECHANICS_FRAMEWORK.zh-CN.md](../docs/MECHANICS_FRAMEWORK.zh-CN.md)。

当前绑定 `v4.3.29.43387 / Steam Build 25096976` 的双指纹，先实现普通熊／乌鸦人类单人经济中的同质食物模型：村民采食、人口规模食耗、冬罚、熊天赋与冬节。完整前提、函数ID和证据等级在 [rules.v1.json](rules.v1.json)。游戏更新后需要重新验证；CLI本身不会验证当前安装或实际局内前提。

在仓库根目录运行：

```powershell
python mechanics/baseline.py --population 10 --gatherers 4 --clan Raven --season non_winter
python mechanics/baseline.py --population 10 --gatherers 4 --clan Raven --season winter
python mechanics/baseline.py --population 10 --gatherers 4 --clan Bear --season winter --winter-festival
```

同一10人状态、4人有效采食，普通非冬季理论净粮约`+8.025/月`，普通冬季约`-23.975/月`。这是固定人口、无其他修正的收支算例，不是冬季库存预测或最优开局。熊冬节只在冬季额外作用于食耗，不能再给粮食生产叠一次同名优惠。

输出放在`model_estimate`，明确`is_native_measurement=false`及`input_context_verified_by_this_module=false`。实际岗位人数、生产资格、所有加成和支付仍需外部证据；不符合前提时不能套用。其他氏族／模式会拒绝，不把未知估成零。

食耗名单、住房人口、可用劳力和单位实体数各有口径。这里的同质简化是假定食耗倍数和等于P；英雄、动物、军队优惠、侦察优惠和特殊氏族均需回到逐单位规则处理。

```powershell
python -m unittest discover -s mechanics -p test_*.py
python scripts/run_tests.py
```

测试验证数学契约与关键反例；不构成控制变量实局验证。后续按[实验计划](../docs/MECHANICS_VALIDATION_PLAN.md)核对，再逐条扩展规则。
