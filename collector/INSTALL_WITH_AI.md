# 给协助安装的 AI

当前为 **0.5.0-alpha 准备版**，支持准备 3v3 本人视角实验采集流程，尚未完成真实 3v3 短测或完整局验收。请先读根目录 [AGENTS.md](../AGENTS.md)、[README.md](../README.md)、[INSTALL_WITH_CODEX](../docs/INSTALL_WITH_CODEX.md) 和 [MULTIPLAYER_3V3](../docs/MULTIPLAYER_3V3.md)。完整安装、版本检查、试采、停止和回传命令以这些文件为准。

本机只读、每人采自己，不导出名单或队友／敌方经济。共享 UUID 与 A／B 席位用于连接各自贡献，不能拿本地 session／单位 UID 跨机合并；只收本队三人也可以。模板默认 `consent_to_share: false`，无自动上传。

`setup_local.py` 与 `doctor.py` 只准备和检查本机环境。用户已授权且进入自己的对局后再采集；不要代操作游戏。先短测并报告各组件缺失，离线通过不等于实局稳定。所有氏族的本人职业可采，住房人口口径目前只核熊／乌鸦；逐人实产与完整加成仍有 null。多人不套用单人增长门槛或盛宴报价。

回传依 [DATA_CONTRIBUTION](../docs/DATA_CONTRIBUTION.md) 生成固定字段审阅包，保留 `experimental_multiplayer_unverified`；只有本人对这局及指定公开仓库的分享授权才执行 fork／PR。不要将原始 captures、旧分享 ZIP、游戏资产或研究原始流直接入库。已有授权按范围继续，不重复确认例行步骤。
