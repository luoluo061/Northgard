# Northgard 本地采集器 0.3.0-alpha

完整安装与使用说明见[仓库README](../README.md)，AI安装流程见[INSTALL_WITH_CODEX](../docs/INSTALL_WITH_CODEX.md)，按游戏理解的数据说明见[GAME_DATA_GUIDE](../docs/GAME_DATA_GUIDE.zh-CN.md)。

默认只读本人资源、人口、单位任务、自有地块建筑和原生经济缓存。新版增加劳动输入、加成上下文、转岗阶段和市政厅增长进度；逐人最终实产、完整动态加成和精确出生时间仍有显式未知项。

先运行本地setup和doctor，再由玩家正常进入熊族单人局做短测。正式采集省略`--seconds`；暂停不计产出时长，结束/Ctrl+C/STOP停止并写最终manifest。默认无网络上传。贡献数据须走本地脱敏投影与验证，保留标签、版本和经验反馈。

源代码包不含游戏文件或历史对局；`research/static`是从玩家自己的已核验安装生成的忽略目录。
