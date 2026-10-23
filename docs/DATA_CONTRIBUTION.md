# 对局数据如何贡献

项目先收集可核查的本人对局，再统一分析人口如何转化为资源与胜利条件。采集器只在本机读取；没有后台回传。[Northgard 仓库](https://github.com/luoluo061/Northgard)现已公开，可以直接 clone 或 fork，无需访问邀请，也无需主仓库的直接写权限即可通过 fork 提交 PR。贡献的数据、标签与经验一旦上传到公开 fork、PR 或主仓库，就会公开可见；请先在本地完成脱敏与审阅。

## 1. 先完成一局并停止采集

按照仓库安装说明启动采集器，由本人正常玩单人局或参与 3v3 多人本人视角实验。每位参与者只采集自己客户端的本人单位、资源、增长和操作；不会获得队友或对手的经济、单位名单与战争迷雾内动态。采集停止后 `manifest.json` 必须有 `recording_state: "stopped"`。采集期间打包会被拒绝。不要把整个 `captures` 文件夹加入 Git，也不要发送 `research-raw.ndjson`、游戏资产、存档或内存转储。

一份贡献只对应一个 `session_key`。同一次采集遇到多局会要求明确选择 `--session-key`；读档、回退、采样中断的 segment 仍保留独立身份，分析时不能跨分段做连续产出积分。原始本地 session／run／segment 字符串在贡献包中改为 24 位 SHA-256 摘要，保留连接关系。

录制停止、读取错误或切换对局时，尚未完成的转岗链以 `tracking_incomplete` 尾记录保留，绑定该单位原来所属的 session／segment 和最后一次真实观测的游戏时间，保留错误／停止状态。它不是取消操作，也不能把故障发生的墙钟时间算进转岗耗时。导出先按尾事件自身身份拆分，再选择对局；旧版没有顶层身份的尾行也按事件内身份恢复，不能混入另一局。

## 2. 填写元数据与自己的经验

单人局复制 [`schemas/metadata.example.json`](../schemas/metadata.example.json)，多人本人视角实验复制 [`schemas/metadata.multiplayer.example.json`](../schemas/metadata.multiplayer.example.json)，到仓库外的 `my-match.json`。`null` 表示没有确认，不能用 `0`、空氏族或猜测填充。`mode` 接受 `singleplayer`、`multiplayer`、`unknown`；原生模式与明确自报模式冲突会被拒绝。多人数据一律标记 `experimental_multiplayer_unverified`，目前仍需真实多人整局校验，不能因为能够导出就称为已验证采集。

模板默认 `consent_to_share: false`，因此直接使用模板会被导出器拒绝。必须由贡献者审阅后明确改为 `true`。这表示同意把此份审阅后的本人游戏数据、标签和经验文字贡献到公开仓库，供公众查看与后续统一分析。文字里不要填写姓名、Steam ID、账号、安装路径、联系方式或凭证。自动检查能发现常见格式，不能替代本人审阅自由文字；已有这一局及本公开仓库的明确上传授权时，不重复确认。

| 字段 | 游戏中的含义 |
|---|---|
| `clan` | 本人氏族的内部 ID，例如 `Bear`；未知可以 `null`，导出会与观测到的氏族核对并补入 |
| `mode` / `difficulty` / `map` | 局的环境，未知保留 `null`；地图名称／大小使用内部短 token，不能粘贴描述或路径 |
| `experience` | 自评新手／中阶／熟练／专家／未知；不当作实力结论 |
| `goal` | 此局尝试什么，例如稳过首冬、快速找知识、测试先港口 |
| `result` | 胜／败／退出／未完成／未知；不可仅从采集停止推断胜负 |
| `tags` | 最多 20 个短标签，可用中文、字母、数字、下划线或连字符 |
| `feedback` | 连接具体游戏时刻的观察、推测、建议、反例；详见 [玩家经验格式](PLAYER_FEEDBACK.md) |
| `match_link` | 可选的自报共享对局标签，连接不同参与者贡献的候选关系；不填可为 `null`，不是游戏原生的全局身份 |

本人原生 `game_context` 独立保存模式、模式变体、难度、地形与特殊模式标记；`match_context` 另存原生模式枚举和起始队伍的聚合人数。这两条原生信息互相校验，贡献者填写的标签不能覆盖它们。`submission.mode_evidence` 分为 `native_singleplayer`、`native_multiplayer`、`contributor_declared_native_context_unavailable`、`unknown`。未捕获原生上下文时允许保留明确自报，仍标记未核验；原生明确多人而贡献者填 `unknown` 时，也必须带多人实验阶段，不能借未知绕过。

如果原生模式和元数据模式都未知，但已有原生起始队伍计数，也保守标记多人实验阶段，`mode_evidence` 仍保留 `unknown`，不会靠人数猜出原生模式。明确单人数据不会因为加入新协议而被升级为已验证多人。

`Team3V3` 是配置枚举；只有完整起始队伍计数支持、且与配置一致，才保留 `team_layout_status: verified_native_roster`。仅有配置、缺少起始名单聚合计数时，布局保持 `null`／`unavailable`。起始人数可能含 AI，不是人类玩家数，也不是完整对战数据。已知原生 2v2／4v4 等布局不能贴成 3v3。即使原生布局可核对，整份多人数据仍保持 `experimental_multiplayer_unverified`，不会自动升级为实测通过。

## 多名参与者如何连接同一场 3v3

先共同约定一个小写 UUIDv4 `shared_match_id`，例如由一位参与者生成后发给同局其他贡献者。各人只填写自己明确知道的队伍标签和位置；不知道就用 `unknown`／`null`。A／B 是参与者协商的标签，位置 1–3 也是自报；原生 `native_local_team_id` 是各客户端起始队伍顺序，不能自动当成 A／B。

```json
"match_link": {
  "shared_match_id": "a1234567-1234-4123-8123-123456789abc",
  "declared_format": "3v3",
  "team_label": "A",
  "player_slot": 1
}
```

不要直接使用上面的示例 ID 当作真实对局 ID。采集时可以使用 `--shared-match-id`、`--declared-format 3v3`、`--team-label A`、`--player-slot 1`，或使用 [3v3 安装说明](MULTIPLAYER_3V3.md) 中的参与者安排。采集器把它们写入本机 manifest 的 `contributor_context`；不会复制队友的数据。导出时把这份自报信息和元数据的 `match_link` 合并，只一方有值时采用它，两方非空但不一致就拒绝。生成的 `submission.match_link` 与 `metadata.match_link` 必须完全相同。

每位参与者仍独立生成一个贡献 UUID、独立脱敏审阅和授权上传。共享对局 ID 只用于建立候选组，不证明大家确实同局；本地 `session_key`、单位 UID 和原生队伍序号不得跨电脑直接连接。多人关联不要求额外许可开关，明确的多人元数据、实验阶段与既有逐局公开分享授权共同适用。

## 3. 生成待审阅包

在仓库根目录用 Python 3.11 或更新版本执行，只有标准库依赖：

```powershell
python scripts/prepare_submission.py --capture captures/my-stopped-run --metadata my-match.json --output submissions-review
```

多局采集时追加 `--session-key <本地 manifest 或观测中的 session_key>`。工具打印本地目录与 ZIP 路径，没有任何上传动作。目录格式为：

```text
submissions-review/
  data/submissions/2026/<每局 UUID>/
    submission.json
    capture-manifest.json
    checksums.sha256
    SUMMARY.md
    player-observations.ndjson.gz
    economic-snapshots.ndjson.gz
    actions.ndjson.gz
    health.ndjson.gz
  <每局 UUID>.zip
```

原采集目录保持原样。新目录采用固定文件名与逐层字段白名单重新生成；`latest.json` 因为重复且可能混入后续状态而不接收入库。unknown key 只会在本地投影时丢弃并计数，绝不会原样复制。贡献包校验阶段遇到任何未知字段都会拒绝。要增加字段，先修改协议、测试和文档，再重新导出，不能绕过校验。

模式与共享标签的扩展是 v1 协议的可选字段，旧单人贡献仍可读取。新多人贡献必须明确保存实验阶段；不得删掉这个标记来混入单人已测数据。

每份贡献总展开数据上限 1 GiB，单流 512 MiB，单条记录 2 MiB，单个压缩数据文件 20 MiB。记录较长时应缩短下一次采集或与维护者讨论外部数据存储；不要手工截断 `.gz`、删掉部分记录后改哈希来假装完整。仓库只保留压缩且已脱敏的贡献，原始研究数据留本机；数量增加后维护者应迁移大体积数据到专用存储并保留目录索引，Git 不是无限数据仓库。

## 4. 本人审阅并验证

先阅读 `SUMMARY.md` 和 `submission.json`。需要抽查逐人字段时，用 Python `gzip.open(..., 'rt', encoding='utf-8')` 阅读压缩 NDJSON，不能把内容当作代码执行。

```powershell
python scripts/validate_submission.py submissions-review/data/submissions/2026/<UUID>
python scripts/validate_submission.py submissions-review/<UUID>.zip
```

SHA-256 检查用于发现意外修改，不是数据真实性或机制因果的证明。ZIP 验证不解压执行文件，只接受精确的平面文件名，拒绝越界路径、重复成员、符号链接、加密成员和超限解压。CI 另外检查字段、类型、游戏历法、本人范围、文件清单、记录数、覆盖边界与重复采集。

## 5. 贡献者明确批准后，才交付

请告诉自己的 Codex：**先完成本地审阅摘要和文件核验。若我已经明确授权把这一局上传到这个仓库，按授权继续；若尚无这份逐局／目标仓库授权，再向我询问，获准后执行 GitHub 写操作。**安装／读取游戏／生成本地包的授权，不等于授权上传或公开数据；已有明确授权时不重复确认。

公开仓库可以直接 fork，再在自己的独立分支提交。下面是审阅后的人类操作示意，不是自动执行脚本：

```powershell
gh repo fork luoluo061/Northgard --clone=false
git remote add my-fork https://github.com/<你的 GitHub 用户名>/Northgard.git
git switch -c data/<UUID>
# 将审阅目录内 data/submissions/YYYY/<UUID> 复制到仓库的同一相对路径
python scripts/validate_submission.py data/submissions --tree
python scripts/build_catalog.py --root data/submissions --output data/catalog.json
git add -- data/submissions/YYYY/<UUID> data/catalog.json
git diff --cached --stat
# 再次核对暂存清单只有这一局的 8 个文件和目录索引
git commit -m "data: add reviewed Bear singleplayer match <UUID>"
git push my-fork data/<UUID>
gh pr create --repo luoluo061/Northgard --head <你的 GitHub 用户名>:data/<UUID> --base main --title "data: Bear singleplayer <UUID>" --body-file submission-pr.txt
```

目录索引保留原有 `matches`，新增 `team_groups` 自报候选组。只把同一共享 ID、相同采集器版本、游戏 build、双文件指纹与声明格式的贡献放在一起；不兼容的共同 ID 会拒绝。同一候选组内已知队伍／位置重复（例如两个 A1）也拒绝，未知位置不会自动补齐。组里有六份标签也不能证明六名人类或证明已完成跨客户端状态同步。统一处理需要继续检查各人的覆盖时间、分段、缺失和自报标签，不能直接把本地单位编号拼成队伍数据。

新建 `submission-pr.txt` 时只写氏族、采集版本、游戏 build、测试目标、结果、覆盖时间、已知中断和是否有经验反馈，不要把原文件路径／账号信息复制进去。默认分支不是 `main` 时使用实际默认分支。重复执行 `git remote add` 已存在会失败，改用已审阅的远端配置，不要随意覆盖其他远端。创建 fork 和 PR 时使用贡献者自己的 GitHub 账户；本工具不要求也不保存 GitHub token。

普通 PR 检查使用 `pull_request`、只读权限、base 分支验证器和候选数据目录，不运行贡献者提交的 Python 或安装脚本，不使用 `pull_request_target`、Actions secrets 或自动合并。**PR 本身可以修改自己的 workflow，所以这个 CI 状态单独不能证明检查没有被绕过。**数据 PR 必须只改 `data/submissions` 和重建的目录索引；代码、schema、CI、文档的改进应另开代码 PR。维护者必须审阅完整改动清单，不能仅看绿色标记。仓库分支保护／CODEOWNERS 等强制规则需要由仓库所有者实际配置，本项目没有替用户开启。

提供额外的可信复核入口：维护者在 GitHub Actions 选择默认分支上的「Validate reviewed match contributions」，手动 `workflow_dispatch` 填 PR 号。这个入口只允许从默认分支运行，读取 PR 的完整改动清单，拒绝夹带代码／CI 改动，固定并核对该 PR 的 head SHA，只检出数据，用默认分支脚本验证。可用命令示意：

```powershell
gh workflow run validate-submissions.yml --ref main -f pr_number=123
```

必须使用实际默认分支并检查复核记录中的 SHA 与待合并 PR 相同。这个 GitHub 手动入口尚需在真实仓库运行验证；本地测试只覆盖解析、打包与校验器。通过可信复核后仍由维护者审阅标签、文字和游戏语义再合并。

## 统一处理时要保留的限制

- 月份来自游戏秒：A800 年 3 月为起点，60 游戏秒一个月；月初／中／末各 20 秒是分析分桶。墙钟时间、暂停次数不能充当人口劳动时长。
- 单位 UID 可连起指派、当前任务、转型、抵达候选与资源缓存更新；采样间可能发生多个动作，不能伪造精确点击时间。
- 库存差额包含持续生产、人口与建筑消耗、殖民／建筑／宴会／招募支出、奖励以及库存上限裁切。不能把库存上升直接归到某个工人头上。
- `total_produced_counter` 含特定默认原因的正向增加，不能当逐人生产账；`produce` 是工作状态候选；模型估计和原生实际到账必须分别标记。
- 任意加成需要说明生效条件、单位、作用对象、叠加顺序与版本证据。熊族的冬季收益、知识、氏族特性不能直接套给乌鸦或其他氏族。
- 本人操作经验是待检验假设。可用窗口比较、控制季节／人口／科技与样本复现来校准，不能把高手建议自动写成已证游戏规则。
