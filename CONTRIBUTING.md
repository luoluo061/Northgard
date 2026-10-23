# 参与采集器改进

先阅读[项目说明](README.md)及[安装流程](docs/INSTALL_WITH_CODEX.md)。当前是0.4.0-alpha实验版；3v3的离线测试不等于真实多人对局已验收。

- 想贡献一局数据：按[数据贡献说明](docs/DATA_CONTRIBUTION.md)完成本地脱敏、标签、审阅和校验，使用独立数据PR。公开上传范围以本人明确授权为准。
- 想提供打法：按[玩家经验格式](docs/PLAYER_FEEDBACK.md)关联年月/月段、本人岗位、氏族和目标，区分观察、推测与建议。欢迎反例。
- 遇到安装或读取问题：使用“安装或采集问题”Issue模板，提供版本和诊断摘要，不提交原始研究文件或机器身份信息。
- 想改代码：fork后单独建分支，读[AGENTS.md](AGENTS.md)，说明问题、最终行为和验证范围。运行`python scripts/run_tests.py`；涉及协议时运行`python scripts/export_submission_schemas.py`同步JSON Schema。数据PR与代码/协议/CI修改分开。

保持读取本人视角、版本双指纹验证、未知值与游戏时间语义。团队标签只用于候选关联，不证明几台电脑的时钟完全同步，也不使本地UID成为全局单位ID。不要通过执行游戏函数、写内存、修改游戏或自动操作去填补缺失数据。
