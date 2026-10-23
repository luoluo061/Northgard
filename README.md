# Northgard 对局数据采集与机制研究

目标是解释 **这一名人口，在这个月、这个氏族和已学知识下，投入哪里能更快达到当前目标**。先建立可信的数据，再建立建议器。

本项目直接读取本地 Northgard 进程中已经存在的数据。无需截图、OCR或手动抄资源；不控制单位、不修改游戏、不执行游戏内函数。当前为 **0.3.0-alpha 开发版**：可安装做本机验证，新增劳动/增长链尚未完成真实整局验收。

## 让你的 Codex 安装

把[仓库链接](https://github.com/luoluo061/Northgard)和下面这段发给你自己的 Codex：

> 请阅读本仓库的 AGENTS.md、docs/INSTALL_WITH_CODEX.md 和数据说明，帮我安装本地采集器。先检查 Windows x64、Python 3.11+ 与游戏版本，生成本地必要规则，再做检查。等我开好单人局后采集。不要控制游戏、上传原始研究数据或更改游戏文件；先把能读到什么和仍缺什么告诉我。之后只有我明确要求回传时，再生成带标签的贡献包并按文档提交。

仓库已公开，任何玩家都可以直接查看、克隆或 fork，无需邀请。提交到仓库的对局贡献与经验反馈也会公开可见；采集仍默认只保存在本机。

## 本机快速开始

```powershell
git clone https://github.com/luoluo061/Northgard.git
cd Northgard
python scripts/setup_local.py
python scripts/doctor.py
```

若 Steam 自动定位失败，使用游戏实际安装目录：

```powershell
python scripts/setup_local.py --game-dir "D:\SteamLibrary\steamapps\common\Northgard"
```

进入自己玩的普通熊族单人局后做短测：

```powershell
python collector/collect.py --seconds 120 --interval 0.5 --out-dir captures/first-check
```

正式记录省略 `--seconds`，使用新的输出目录。采集到本局结束会停止；也可按 Ctrl+C，或在输出目录建立 `STOP` 空文件。等待最终 manifest 写出再处理数据。暂停期间继续保存状态，但不会增加游戏时间收益。

当前只接受已核对的 `v4.3.29.43387 / Steam Build 25096976` 和对应双指纹。游戏升级后会拒绝读取，不能改哈希强行兼容。熊/乌鸦普通人口口径已有基础支持，其他氏族与特殊模式的未知项保持未知。

## 采什么、怎样理解

| 数据层 | 用途 | 当前界限 |
|---|---|---|
| 库存、原生产消缓存、容量、支付状态 | 区分运营净流与一次性支出 | 非原子读取；全局产消不等于逐人账单 |
| 人口、职业、任务、指派、位置、生产门控 | 连接“派谁去干什么”与资源变化 | 指派/候选不等于已结算产量 |
| 科技、工具、状态、本人地块加成输入 | 确定劳动收益的适用条件 | 动态getter没有完整持久分项表；未解码项不补零 |
| 调岗阶段、进度与状态变化 | 区分指派、转职、生产候选恢复 | 游戏秒区间；不是点击日志或纯走路时长 |
| 市政厅增长进度、幸福与阻塞输入 | 解释住房、幸福、出生等待 | 完整增长加成与反事实ETA需持续验证 |
| 已探索地块、自己的建筑与殖民状态 | 连接取得土地→施工→完工→上岗 | 不导出未探索地图和未验证的敌方状态 |

详细解释、例子、字段证据和玩家可反馈的问题见 [按游戏机制理解采集数据](docs/GAME_DATA_GUIDE.zh-CN.md)。逐人实产限制见 [UNIT_PRODUCTION_LIMITS.md](docs/UNIT_PRODUCTION_LIMITS.md)。

## 数据回传与经验反馈

采集默认仅留在本机，不自动联网。回传流程是：**结束采集 → 填标签与经验 → 本地脱敏投影 → 验证并审阅 → 用户授权 → 分支/PR → 统一入库处理**。

- [数据贡献协议](docs/DATA_CONTRIBUTION.md)：固定字段、压缩与校验、标签、公开分享范围、fork和PR。
- [玩家经验反馈](docs/PLAYER_FEEDBACK.md)：用年月/游戏秒连接观察、推测、经验、反例。
- `data/submissions/`：只放验证过的贡献包，不放整份 captures 或研究原始文件。
- `scripts/build_catalog.py`：构建后续统一处理的可复算目录索引。

## 开发和验证

```powershell
python scripts/run_tests.py
python collector/build_release.py --source --out-dir dist
```

只依赖 Python 标准库。游戏规则在安装时从玩家自己的已核验资源包生成，保存在忽略目录，不发布游戏PAK、EXE、字节码、翻译或美术资源。

实测范围与下一局验收清单见 [VALIDATION_STATUS.md](docs/VALIDATION_STATUS.md)。当前没有自动最优运营建议，也不把离线测试通过描述为新增字段已完成实局验证。
